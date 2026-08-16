#!/usr/bin/env python3
"""Index EnterpriseRAG-Bench documents with SEOCHO and emit the extracted graph.

This replaces the shortcut the first ERB adapter took. That version built the
graph arm from the dataset's own `answer_facts`, which measures how a model
reads a form we did not produce — it routes around the thing this project
actually builds. The question worth answering is whether OUR indexing recovers,
from raw enterprise records, the actors and decisions the questions ask about.
GraphRAG-Bench ships gold triples; ERB does not, so we extract our own and are
then accountable for them.

The ontology is derived from what the questions demand, not from what is easy
to extract. Reading the three aggregation strata:

  conflicting_info   "an earlier suggestion was 20%, but the applied target is
                     30%" — needs a decision to carry a VALUE and a STATUS, and
                     a SUPERSEDES edge. A flat triple set cannot answer this at
                     all: both numbers are true statements about the corpus and
                     only their relative recency separates them.
  project_related    "throttling was triggered by X during Y, mitigated by Z" —
                     needs a causal chain across incident, cause and mitigation,
                     with the customer and region attached.
  completeness       "first pause the rollout, then re-pin, then redeploy" —
                     needs ORDER, which is the one thing a set of triples
                     discards by default.

So `Decision.status`, `SUPERSEDES` and `Step.position` are not ontology
decoration; each one is the minimum structure some stratum's gold answer
depends on. If extraction drops them the graph arm cannot win those strata no
matter how good retrieval is, and that is a finding about our indexing rather
than about graphs.

Scope: the gold documents of the selected questions — 322 documents, 2.2M
characters. Retrieval is held perfect on purpose, exactly as in the arms
comparison, so a difference is attributable to extraction rather than to search.

Usage:
    export MARA_API_KEY=...
    scripts/serve_track/erb_index.py \\
        --parquet ~/.cache/huggingface/hub/datasets--onyx-dot-app--.../snapshots/<id> \\
        --types conflicting_info project_related completeness \\
        --out outputs/serve_track/erb_extracted.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

SCHEMA_VERSION = 1


def build_ontology():
    """Actors, decisions, and the three things the gold answers turn on.

    Kept deliberately small. A large ontology raises extraction recall on paper
    and lowers agreement in practice (`ADR-0154` measured ontology guidance
    *lowering* cross-model agreement), and every type here has to earn its place
    by being load-bearing for a question we are actually scoring.
    """
    from seocho import NodeDef, Ontology, P, RelDef

    return Ontology(
        name="enterprise_decisions",
        nodes={
            "Person": NodeDef(
                description="A named individual: an engineer, manager, or customer contact.",
                properties={"name": P(str, unique=True), "role": P(str), "org": P(str)},
                identity_keys=["name"],
            ),
            "Org": NodeDef(
                description="A company or team — the vendor, a customer, or an internal group.",
                properties={"name": P(str, unique=True), "kind": P(str)},
                identity_keys=["name"],
            ),
            "System": NodeDef(
                description="A service, component, pool, or environment that work is done to.",
                properties={"name": P(str, unique=True), "region": P(str)},
                identity_keys=["name"],
            ),
            "Decision": NodeDef(
                description=(
                    "A choice that was proposed, applied, or reversed. Carries the "
                    "value decided and whether it is the CURRENT one — a superseded "
                    "decision is still a true statement about the corpus, and only "
                    "status separates it from the answer."
                ),
                properties={
                    "name": P(str, unique=True),
                    "value": P(str),
                    "status": P(str),   # proposed | applied | superseded | reverted
                    "decided_on": P(str),
                },
                identity_keys=["name"],
            ),
            "Incident": NodeDef(
                description="An observed failure or degradation, with its symptom.",
                properties={"name": P(str, unique=True), "symptom": P(str), "started": P(str)},
                identity_keys=["name"],
            ),
            "Step": NodeDef(
                description=(
                    "One action in a procedure. `position` exists because ordering "
                    "is what a triple set discards, and the completeness stratum's "
                    "gold answers are ordered."
                ),
                properties={
                    "name": P(str, unique=True),
                    "position": P(int),
                    "procedure": P(str),
                },
                identity_keys=["name", "procedure"],
            ),
        },
        relationships={
            "DECIDED": RelDef("Person", "Decision",
                              description="The person who made or applied the decision."),
            # Direction decides the answer here, so it is stated rather than
            # left to the extractor: the NEWER decision is the source and the
            # one it replaces is the target. Reversed, a conflicting_info item
            # returns the stale value while looking equally well-formed.
            "SUPERSEDES": RelDef("Decision", "Decision",
                                 description="The newer decision replaces the older one. "
                                             "Source is current, target is obsolete.",
                                 source_role="replacement", target_role="replaced"),
            "APPLIES_TO": RelDef("Decision", "System",
                                 description="The system, pool, or region the decision governs."),
            "AFFECTS": RelDef("Incident", "Org",
                              description="The customer or team the incident impacted."),
            "OCCURRED_ON": RelDef("Incident", "System",
                                  description="Where the incident was observed."),
            "CAUSED_BY": RelDef("Incident", "Decision",
                                description="The change or decision that triggered the incident."),
            "MITIGATED_BY": RelDef("Incident", "Decision",
                                   description="The action taken to resolve or contain it."),
            "WORKS_FOR": RelDef("Person", "Org"),
            "PRECEDES": RelDef("Step", "Step",
                               description="This step must be completed before the next one. "
                                           "Source runs first.",
                               source_role="earlier", target_role="later"),
            "PART_OF": RelDef("Step", "System",
                              description="The system the procedure operates on."),
        },
    )


def load_gold_documents(parquet_dir: Path, types: List[str]) -> Dict[str, Dict[str, Any]]:
    import pyarrow.parquet as pq

    questions = pq.read_table(parquet_dir / "data" / "questions" / "test.parquet").to_pylist()
    selected = [r for r in questions if r["question_type"] in types]
    wanted = {i for r in selected for i in r["expected_doc_ids"]}

    table = pq.read_table(parquet_dir / "data" / "documents" / "test.parquet",
                          columns=["doc_id", "source_type", "title", "content"])
    docs: Dict[str, Dict[str, Any]] = {}
    for doc_id, source, title, content in zip(
        table["doc_id"].to_pylist(), table["source_type"].to_pylist(),
        table["title"].to_pylist(), table["content"].to_pylist()
    ):
        if doc_id in wanted:
            docs[doc_id] = {"source_type": source, "title": title, "content": content}
    return docs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--types", nargs="+",
                        default=["conflicting_info", "project_related", "completeness"])
    parser.add_argument("--out", type=Path,
                        default=Path("outputs/serve_track/erb_extracted.jsonl"))
    parser.add_argument("--model", default="MiniMax-M2.7")
    parser.add_argument("--base-url", default="https://api.cloud.mara.com/v1")
    parser.add_argument("--max-chars", type=int, default=6000,
                        help="per-document cap fed to one extraction call")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=8,
                        help="documents extracted in parallel. Serial extraction "
                             "runs ~2 min/doc, which is 10 hours for this corpus")
    parser.add_argument("--retries", type=int, default=2,
                        help="retries when the model returns reasoning instead of JSON")
    parser.add_argument("--enforcement", default="guided",
                        choices=["open", "guided", "strict"],
                        help="ontology enforcement; recorded in the output")
    args = parser.parse_args()

    key = os.environ.get("MARA_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        raise SystemExit("MARA_API_KEY is not set")

    from seocho.index.extraction_engine import CanonicalExtractionEngine
    from seocho.store.llm import create_llm_backend

    ontology = build_ontology()
    llm = create_llm_backend(provider="openai", model=args.model,
                             api_key=key, base_url=args.base_url)
    # `guided` rather than `strict`: strict rejects anything outside the declared
    # vocabulary, and on raw enterprise prose that discards most of the document.
    # ADR-0154 also measured ontology guidance LOWERING cross-model agreement, so
    # the looser setting is the honest default and the enforcement level is
    # recorded per run rather than assumed.
    engine = CanonicalExtractionEngine(llm=llm, ontology=ontology, enforcement=args.enforcement)

    docs = load_gold_documents(args.parquet, args.types)
    items = sorted(docs.items())
    if args.limit:
        items = items[: args.limit]
    print(f"extracting from {len(items)} gold documents with {args.model}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    totals = {"nodes": 0, "rels": 0, "failed": 0, "retried": 0}
    started = time.perf_counter()
    lock = threading.Lock()

    def extract_one(pair):
        doc_id, doc = pair
        text = f"{doc['title']}\n\n{doc['content']}"[: args.max_chars]
        last = None
        for attempt in range(args.retries + 1):
            try:
                graph = engine.extract(
                    text,
                    category=doc["source_type"],
                    metadata={"doc_id": doc_id, "source_type": doc["source_type"]},
                )
                return doc_id, doc, graph.get("nodes") or [], graph.get("relationships") or [], None, attempt
            except Exception as exc:  # noqa: BLE001 — one bad document must not end the run
                # MiniMax-M2.7 is a reasoning model and sometimes emits its
                # reasoning where the JSON should be ("Let me analyze this
                # text..."). That is transient, so it is retried rather than
                # counted as an extraction failure on the first try.
                last = f"{type(exc).__name__}: {exc}"
        return doc_id, doc, [], [], last, args.retries

    done = 0
    with args.out.open("w", encoding="utf-8") as handle, \
            ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(extract_one, pair) for pair in items]
        for future in as_completed(futures):
            doc_id, doc, nodes, rels, error, attempts = future.result()
            with lock:
                done += 1
                totals["nodes"] += len(nodes)
                totals["rels"] += len(rels)
                totals["retried"] += 1 if attempts else 0
                if error:
                    totals["failed"] += 1
                handle.write(json.dumps({
                    "schema_version": SCHEMA_VERSION,
                    "doc_id": doc_id,
                    "source_type": doc["source_type"],
                    "enforcement": args.enforcement,
                    "title": doc["title"],
                    "nodes": nodes,
                    "relationships": rels,
                    "attempts": attempts + 1,
                    "error": error,
                }, ensure_ascii=False) + "\n")
                handle.flush()
                if done % 20 == 0 or done == len(items):
                    rate = done / max(time.perf_counter() - started, 1e-6)
                    print(f"  [{done}/{len(items)}] nodes={totals['nodes']} "
                          f"rels={totals['rels']} retried={totals['retried']} "
                          f"failed={totals['failed']} ({rate*60:.1f} docs/min)", flush=True)

    elapsed = time.perf_counter() - started
    print(f"\nwrote {args.out} in {elapsed/60:.1f} min")
    print(f"  nodes={totals['nodes']}  relationships={totals['rels']}  "
          f"retried={totals['retried']}  failed={totals['failed']}/{len(items)}")
    if totals["failed"]:
        print("  Failed documents are written with empty node and relationship "
              "lists and a recorded error, never dropped — a silently shorter "
              "graph would read as an extraction quality result.")


if __name__ == "__main__":
    main()
