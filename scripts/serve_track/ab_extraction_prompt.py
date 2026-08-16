#!/usr/bin/env python3
"""A/B the extraction prompt: does stating requirements change what gets extracted?

Two changes are under test, crossed, so each one's contribution is separable
rather than inferred from a single before/after.

  requirements   Purpose, competency questions, and modelling decisions in the
                 system prompt. `Ontology(description=...)` was accepted and
                 stored and then dropped at exactly the point it would help, so
                 the model received the formalised artefact -- a list of classes
                 and relations -- with none of the requirements analysis that
                 produced it. Keet's micro-level methodologies (OntoSpec, OD101,
                 DiDOn) treat purpose, query types and modelling decisions as
                 determining the axioms rather than preceding them; this package
                 already scores coverage against competency questions and simply
                 never told the extractor what they were.

  generic_rules  The label-selection rules carried FinDER literals --
                 FinancialMetric, Revenue, OperatingIncome, EPS,
                 "'LegalEntity' not 'Company'" -- shipped verbatim to every
                 extraction regardless of domain, naming classes absent from the
                 allowed list the model was just given. The output-format
                 example also used `"label": "EntityType"`, and the baseline run
                 emitted a node that was literally `EntityType{name: "Entity
                 Name"}`, so the example leaked into the data.

Everything is scored by counting, never by a judge. Four metrics, each chosen
because a specific failure in the baseline run makes it load-bearing:

  status_vocab     distinct `Decision.status` values, and distinct after case
                   folding. The baseline produced CURRENT/current/SUPERSEDED/
                   superseded/proposed/applied/pending/mitigation -- a filter on
                   `status='superseded'` misses half of them. `P` has no enum
                   argument, so a modelling decision in prose is the only
                   channel available.
  supersedes       SUPERSEDES edges. The conflicting_info stratum is unanswerable
                   without them; the baseline found 12 across 51 documents.
  off_ontology     nodes whose label is not in the ontology. Directly measures
                   the EntityType leak.
  prompt_tokens    the FinDER rules are not free.

Same documents, same model, temperature 0. Only the prompt differs.

Usage:
    export MARA_API_KEY=...
    scripts/serve_track/ab_extraction_prompt.py --parquet <dir> --docs 20
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA_VERSION = 1

# The requirements a human ontologist would have written down first. Each line
# is traceable to a question the benchmark actually asks.
PURPOSE = (
    "Recover, from raw enterprise records, who decided what and which decision "
    "is currently in force, so a reader can answer operational questions without "
    "reading every document."
)
COMPETENCY_QUESTIONS = [
    "For a given system or pool, which configuration value is CURRENTLY applied, "
    "and which earlier value did it replace?",
    "What caused a given incident, and what action mitigated it?",
    "In what ORDER must the steps of a procedure be performed?",
    "Who made a decision, and which organisation do they work for?",
]
MODELLING_DECISIONS = [
    "status is an attribute of Decision, not a separate class. Use exactly one "
    "of: proposed, applied, superseded, reverted. Lowercase.",
    "SUPERSEDES runs from the NEWER decision to the one it replaces. When a "
    "document mentions an earlier value and a later one, emit both Decisions and "
    "the edge between them rather than only the current value.",
    "Step.position is 1-based and reflects execution order, not document order.",
    "A person and the organisation they belong to are separate nodes joined by "
    "WORKS_FOR; do not fold the org into Person.org alone.",
]


def make_ontology(with_requirements: bool):
    import erb_index

    base = erb_index.build_ontology()
    if not with_requirements:
        return base
    base.description = PURPOSE
    base.annotations = {
        "competency_questions": COMPETENCY_QUESTIONS,
        "modelling_decisions": MODELLING_DECISIONS,
    }
    # The rendered ontology block is cached per instance; drop it so the new
    # annotations are picked up rather than silently ignored.
    for attr in ("_render_cache", "_system_prompt_cache"):
        if hasattr(base, attr):
            getattr(base, attr).clear()
    return base


def measure(rows: List[Dict[str, Any]], allowed_labels: set) -> Dict[str, Any]:
    ok = [r for r in rows if not r.get("error")]
    statuses = [
        str((n.get("properties") or {}).get("status") or "")
        for r in ok for n in r["nodes"] if n.get("label") == "Decision"
    ]
    statuses = [s for s in statuses if s]
    off = [n.get("label") for r in ok for n in r["nodes"]
           if n.get("label") not in allowed_labels]
    steps = [n for r in ok for n in r["nodes"] if n.get("label") == "Step"]
    positioned = [n for n in steps
                  if (n.get("properties") or {}).get("position") is not None]
    return {
        "docs": len(rows),
        "failed": len(rows) - len(ok),
        "nodes": sum(len(r["nodes"]) for r in ok),
        "rels": sum(len(r["relationships"]) for r in ok),
        "supersedes": sum(1 for r in ok for x in r["relationships"]
                          if x.get("type") == "SUPERSEDES"),
        "status_values": len(set(statuses)),
        "status_values_folded": len({s.lower() for s in statuses}),
        "status_off_vocab": sum(
            1 for s in statuses
            if s.lower() not in {"proposed", "applied", "superseded", "reverted"}
        ),
        "status_total": len(statuses),
        "off_ontology": len(off),
        "step_position_set": f"{len(positioned)}/{len(steps)}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--docs", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--model", default="MiniMax-M2.7")
    parser.add_argument("--base-url", default="https://api.cloud.mara.com/v1")
    parser.add_argument("--max-chars", type=int, default=6000)
    parser.add_argument("--out", type=Path,
                        default=Path("outputs/serve_track/ab_prompt.jsonl"))
    args = parser.parse_args()

    key = os.environ.get("MARA_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        raise SystemExit("MARA_API_KEY is not set")

    import erb_index
    from seocho.index.extraction_engine import CanonicalExtractionEngine
    from seocho.store.llm import create_llm_backend

    docs = erb_index.load_gold_documents(
        args.parquet, ["conflicting_info", "project_related", "completeness"])
    # Deterministic slice: sorted by id, so the same documents every run.
    items = sorted(docs.items())[: args.docs]
    print(f"{len(items)} documents x 2 prompt variants\n")

    llm = create_llm_backend(provider="openai", model=args.model,
                             api_key=key, base_url=args.base_url)
    allowed = set(erb_index.build_ontology().nodes.keys())

    arms = {
        "baseline": make_ontology(with_requirements=False),
        "requirements": make_ontology(with_requirements=True),
    }
    results: Dict[str, List[Dict[str, Any]]] = {}
    prompt_tokens: Dict[str, int] = {}
    lock = threading.Lock()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    handle = args.out.open("w", encoding="utf-8")

    for arm, ontology in arms.items():
        engine = CanonicalExtractionEngine(llm=llm, ontology=ontology,
                                           enforcement="guided")
        system, _ = engine._render_extraction_prompts(
            text="probe", category="general", metadata=None, extra_context=None)
        prompt_tokens[arm] = len(system) // 4  # rough; chars/4, stated as such

        def extract_one(pair, engine=engine):
            doc_id, doc = pair
            text = f"{doc['title']}\n\n{doc['content']}"[: args.max_chars]
            for _ in range(3):
                try:
                    graph = engine.extract(text, category=doc["source_type"],
                                           metadata={"doc_id": doc_id})
                    return {"doc_id": doc_id,
                            "nodes": graph.get("nodes") or [],
                            "relationships": graph.get("relationships") or [],
                            "error": None}
                except Exception as exc:  # noqa: BLE001
                    last = f"{type(exc).__name__}: {exc}"
            return {"doc_id": doc_id, "nodes": [], "relationships": [], "error": last}

        started = time.perf_counter()
        rows: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [pool.submit(extract_one, pair) for pair in items]
            for future in as_completed(futures):
                row = future.result()
                with lock:
                    rows.append(row)
                    handle.write(json.dumps({"arm": arm, **row}, ensure_ascii=False) + "\n")
                    handle.flush()
        results[arm] = rows
        print(f"  {arm:14s} done in {(time.perf_counter()-started)/60:.1f} min")

    handle.close()

    print("\n=== measured, no judge involved ===")
    metrics = {arm: measure(rows, allowed) for arm, rows in results.items()}
    keys = ["docs", "failed", "nodes", "rels", "supersedes", "status_total",
            "status_values", "status_values_folded", "status_off_vocab",
            "off_ontology", "step_position_set"]
    print(f"  {'metric':22s}" + "".join(f"{a:>16s}" for a in arms))
    for key in keys:
        print(f"  {key:22s}" + "".join(f"{str(metrics[a][key]):>16s}" for a in arms))
    print(f"  {'prompt_chars/4':22s}" + "".join(f"{prompt_tokens[a]:>16d}" for a in arms))

    print("\nstatus_off_vocab counts values outside proposed|applied|superseded|"
          "reverted.\nstatus_values vs status_values_folded exposes case splits, "
          "which a filter\nsilently loses. Both are the thing `P` cannot declare.")


if __name__ == "__main__":
    main()
