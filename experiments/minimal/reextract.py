#!/usr/bin/env python3
"""Re-extract the corpus under four ontologies, with the whole path traced.

This is the paid run that decides whether the ontology helps. Four arms (see
arms.py), three extractor models, one shared case sample. Everything except the
ontology is held fixed.

What gets traced, and why it had to be built
--------------------------------------------
The existing indexer prints a per-case summary and nothing else, so the only
evidence that extraction happened the way it was described was the node count
it reported. Here every step leaves a record:

    llm.extract.*   the extraction call, request written before it is issued
    llm.link.*      the second, separate call that resolves duplicate entities
    index.case      the per-case rollup, read back from the database afterwards
    driver.jsonl    every statement the Neo4j driver actually sent, with the
                    server's own t_first and t_last

The driver-level records come from observe.DriverLogHandler, so `driver.jsonl`
holds the real Cypher and the server's own t_first/t_last rather than our
round-trip guess.

Isolation
---------
One database per (arm, model), twelve in all, on the single DozerDB instance —
that is what its multi-database support is for, and no extra container is
involved. This follows the rule in CLAUDE.md 13. Belt and braces on top: the
write is MERGE (n:L {id: $id, _workspace_id: $ws}) (seocho/store/graph.py:386)
and uniqueness constraints are composite on (property, _workspace_id)
(seocho/ontology.py:1641), so even a shared database could not merge one arm's
entity onto another's. Every read here filters on `_workspace_id` as well, so a
leak would show up as a count that does not match its workspace.

The instance was at its database cap when this was written and thirteen dead or
unreferenced databases were dropped to make room; see scripts/ops/audit_databases.py
for what was removed and on what evidence.

Concurrency
-----------
Work is split by (arm, model), one worker each. That split is chosen so workers
never contend: each writes to its own database, and the pair is also the unit
the rate limit applies to, since a worker issues one model call at a time. Cases
inside a worker stay sequential. The default of six workers keeps two of the
twelve pairs in flight per model, well inside the tightest MARA budget
(DeepSeek-V3.1 at 1500 requests a day against roughly 500 for its whole share of
this run).

Resume
------
A finished (arm, model, case) writes a partial and is skipped on re-run, matched
on the arm's ontology hash and the prompt hash. Re-running the same command
continues; --no-resume forces recomputation. API quota is scarce and this run is
paid for by the user.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from dotenv import dotenv_values  # noqa: E402

for _key, _value in dotenv_values(ROOT / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)

import arms  # noqa: E402

OUT_ROOT = ROOT / "outputs/minimal"
PARTIAL_ROOT_BASE = ROOT / "outputs/evaluation/mdm_fedcat"
URI = "bolt://localhost:7687"

MODELS = {
    "deepseek": "DeepSeek-V3.1",
    "gptoss": "gpt-oss-120b",
    "minimax27": "MiniMax-M2.7",
}
# Categories are uneven in the source pool, so the sample is drawn per category
# and the per-category quota is fixed here rather than derived from the sample,
# which would let the sample size change what "balanced" means.
SAMPLE_PER_CATEGORY = 2   # overridden by --per-category
MIN_REFERENCES = 2


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def database_for(arm: str, model_key: str, tag: str) -> str:
    return f"arm{tag}{arm.lower()}{model_key}"


def workspace_for(arm: str, model_key: str, case_id: str, tag: str) -> str:
    # The tag is in the workspace id as well as the database name. Without it a
    # rerun under a changed ontology merges into the previous run's nodes, since
    # the merge key is (id, _workspace_id) and neither would have moved.
    return f"arm{tag}-{arm.lower()}-{model_key}-{case_id}"


def select_cases(limit: int, per_category: int = SAMPLE_PER_CATEGORY) -> list[dict]:
    """A fixed, category-balanced sample, identical for every arm and model.

    Two rules, both fixed before any arm was run. Balance across the eight
    categories, so no category's vocabulary dominates. And prefer cases with at
    least two reference passages, because a single passage yields a graph too
    small for two models to have any chance of describing the same fact twice,
    which would make the comparability measure bottom out for reasons that have
    nothing to do with the ontology. Within those constraints the pick is
    random on seed 42 rather than the lowest case id, which would have taken
    whichever cases happen to sort first.

    The preference is a preference and not a filter, because it is not evenly
    satisfiable. Every one of the 490 Risk cases has exactly one reference, so
    a hard filter would drop Risk from the sample entirely — and Risk is the
    category the vocabulary measurement already flagged as furthest from FIBO's
    scope, which makes it the last one to lose. Categories that fall back are
    named in the returned metadata so the exception is visible in the artifact
    rather than inferred from a missing row.
    """
    import importlib.util
    import random

    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = module.load_cases_full(seed=42)

    by_category: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        if case["references"]:
            by_category[case["category"]].append(case)

    chosen: list[dict] = []
    fell_back: list[str] = []
    for category in sorted(by_category):
        pool = sorted(by_category[category], key=lambda c: c["case_id"])
        rich = [c for c in pool if len(c["references"]) >= MIN_REFERENCES]
        picker = random.Random(f"42-{category}")
        if len(rich) >= per_category:
            chosen += picker.sample(rich, per_category)
        else:
            fell_back.append(category)
            chosen += rich
            remaining = [c for c in pool if c not in rich]
            chosen += picker.sample(
                remaining, min(per_category - len(rich), len(remaining)))
    chosen.sort(key=lambda c: c["case_id"])
    for case in chosen:
        case["single_reference_fallback"] = case["category"] in fell_back
    return (chosen[:limit] if limit else chosen), fell_back


def ensure_database(name: str) -> dict[str, Any]:
    """Confirm the target database exists and report what is already in it.

    Creation is attempted but the instance is at its cap, so the normal outcome
    is that the database is already there. A pre-existing node count is returned
    rather than ignored: this run must not be read as having produced nodes that
    were there before it started.
    """
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(URI, auth=auth())
    try:
        with driver.session(database="system") as session:
            present = [r["name"] for r in
                       session.run("SHOW DATABASES YIELD name RETURN name")]
            created = False
            if name not in present:
                # No WAIT: DozerDB rejects it as an unsupported administration
                # command, which is what the first attempt at this run died on.
                session.run(f"CREATE DATABASE {name} IF NOT EXISTS").consume()
                created = True
        with driver.session(database=name) as session:
            existing = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            mine = session.run(
                "MATCH (n) WHERE n._workspace_id STARTS WITH 'arm' "
                "RETURN count(n) AS c").single()["c"]
        return {"database": name, "created": created,
                "nodes_before": int(existing), "arm_nodes_before": int(mine)}
    finally:
        driver.close()


def build_arms(class_limit: int) -> dict[str, dict[str, Any]]:
    fibo = arms.parse_fibo()
    documents, _ = arms.load_corpus_text()
    classes = arms.scope_to_corpus(fibo, documents, class_limit, 20)
    by_iri = {c["iri"]: c["node"] for c in classes}
    frequency = arms.document_frequency(
        documents,
        {arms.normalize(b["label"]) for b in fibo["object_properties"].values()
         if arms.normalize(b["label"])})
    relations = arms.scope_relations(fibo, by_iri, frequency, 40)
    declared = arms.datatype_domains(arms.TTL)
    hierarchy = arms.subsumption_within(fibo, by_iri)

    built = [
        arms.build_arm_a(),
        arms.build_arm_b(),
        arms.build_fibo_arm(classes, relations, fibo=fibo, declared=declared),
        arms.build_fibo_arm(classes, relations, synonyms=True, fibo=fibo,
                            declared=declared),
        arms.build_fibo_arm(classes, relations, subsumption=hierarchy, fibo=fibo,
                            declared=declared),
    ]
    return {a["arm"]: {**a, "context": arms.context_of(a)} for a in built}


class TracedLLM:
    """Record every model call the indexer makes, without changing what it does.

    The indexer issues two kinds of call per chunk and reports neither: one to
    extract nodes and relationships, and a second, `LinkingStrategy`, that asks
    the model to point duplicate entities at a canonical id. Both are billed and
    both change the graph, so both belong in the trace. They are told apart by
    the linking prompt's own instruction text rather than by call order, which
    would be wrong whenever extraction retries.

    The request is written before it is issued, so a call that hangs or dies
    still leaves what was sent.
    """

    LINK_MARKER = "linked_id"

    def __init__(self, inner, run, case_id: str, arm: str, model_key: str) -> None:
        self._inner = inner
        self._run = run
        self._case = case_id
        self._arm = arm
        self._model_key = model_key
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def complete(self, *, system: str, user: str, **kwargs: Any):
        kind = "link" if self.LINK_MARKER in system else "extract"
        index = len(self.calls) + 1
        started = time.perf_counter()
        self._run._append({
            "stage": f"llm.{kind}.request", "status": "sent",
            "arm": self._arm, "model_key": self._model_key, "case": self._case,
            "call": index, "system_chars": len(system), "user_chars": len(user),
            "temperature": kwargs.get("temperature"),
            "system_head": system[:400], "user_head": user[:1200],
        })
        try:
            response = self._inner.complete(system=system, user=user, **kwargs)
        except Exception as exc:  # noqa: BLE001 — recorded, then re-raised
            elapsed = round(time.perf_counter() - started, 3)
            self._run._append({
                "stage": f"llm.{kind}.response", "status": "error",
                "arm": self._arm, "model_key": self._model_key,
                "case": self._case, "call": index, "seconds": elapsed,
                "error": repr(exc)})
            self.calls.append({"kind": kind, "status": "error",
                               "seconds": elapsed})
            raise
        elapsed = round(time.perf_counter() - started, 3)
        text = getattr(response, "content", None) or getattr(response, "text", "") or ""
        usage = getattr(response, "usage", None) or {}
        record = {
            "stage": f"llm.{kind}.response", "status": "ok",
            "arm": self._arm, "model_key": self._model_key, "case": self._case,
            "call": index, "seconds": elapsed, "response_chars": len(str(text)),
            "prompt_tokens": (usage.get("prompt_tokens")
                              if isinstance(usage, dict) else None),
            "completion_tokens": (usage.get("completion_tokens")
                                  if isinstance(usage, dict) else None),
            "response_head": str(text)[:1200],
        }
        self._run._append(record)
        self.calls.append({"kind": kind, "status": "ok", "seconds": elapsed,
                           "completion_tokens": record["completion_tokens"]})
        return response


def index_one(run, arm: dict[str, Any], model_key: str, model: str,
              case: dict, extraction_tmpl, database: str,
              tag: str) -> dict[str, Any]:
    """Extract one case's reference text into this arm's store."""
    from seocho import Seocho
    from seocho.store.graph import Neo4jGraphStore
    from seocho.store.llm import create_llm_backend

    workspace = workspace_for(arm["arm"], model_key, case["case_id"], tag)
    record: dict[str, Any] = {
        "arm": arm["arm"], "ontology": arm["id"], "model_key": model_key,
        "model": model, "case_id": case["case_id"], "category": case["category"],
        "workspace_id": workspace, "database": database,
        "ontology_hash": arm["context"]["ontology_hash"],
        "references": len(case["references"]),
    }
    started = time.perf_counter()
    client = None
    llm = None
    try:
        store = Neo4jGraphStore(URI, *auth())
        llm = TracedLLM(create_llm_backend(provider="mara", model=model),
                        run, case["case_id"], arm["arm"], model_key)
        client = Seocho(ontology=arm["ontology"], graph_store=store,
                        workspace_id=workspace, llm=llm,
                        extraction_prompt=extraction_tmpl)
        client.default_database = database
        # Constraints are what make the merge key mean anything. A silent
        # failure here would look like successful extraction with no dedup, so
        # it is recorded rather than swallowed.
        try:
            store.ensure_constraints(arm["ontology"], database=database)
            record["constraints"] = "ok"
        except Exception as exc:  # noqa: BLE001
            record["constraints"] = f"{type(exc).__name__}: {exc}"
        for reference in case["references"]:
            client.add(reference, user_id=workspace)
        nodes = store.query(
            "MATCH (n {_workspace_id:$w}) RETURN count(n) AS c",
            params={"w": workspace}, database=database)
        rels = store.query(
            "MATCH ({_workspace_id:$w})-[x]->() RETURN count(x) AS c",
            params={"w": workspace}, database=database)
        declared = set(arm["ontology"].nodes)
        typed = store.query(
            "MATCH (n {_workspace_id:$w}) UNWIND labels(n) AS l "
            "RETURN l AS label, count(*) AS c",
            params={"w": workspace}, database=database)
        with_period = store.query(
            "MATCH (n {_workspace_id:$w}) WHERE n.period IS NOT NULL "
            "AND n.period <> '' RETURN count(n) AS c",
            params={"w": workspace}, database=database)
        record.update({
            "status": "ok",
            "llm_calls": len(llm.calls),
            "extract_calls": sum(1 for c in llm.calls if c["kind"] == "extract"),
            "link_calls": sum(1 for c in llm.calls if c["kind"] == "link"),
            "llm_seconds": round(sum(c["seconds"] for c in llm.calls), 3),
            "completion_tokens": sum(c.get("completion_tokens") or 0
                                     for c in llm.calls),
            "nodes": int(nodes[0]["c"]), "rels": int(rels[0]["c"]),
            "declared_label_nodes": sum(int(r["c"]) for r in typed
                                        if r["label"] in declared),
            "undeclared_labels": sorted({r["label"] for r in typed
                                         if r["label"] not in declared}),
            "period_filled": int(with_period[0]["c"]),
        })
    except Exception as exc:  # noqa: BLE001 — recorded, never imputed (§20.2)
        record.update({"status": "error", "error": f"{type(exc).__name__}: {exc}",
                       "nodes": 0, "rels": 0,
                       "llm_calls": len(llm.calls) if llm is not None else 0})
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
    record["seconds"] = round(time.perf_counter() - started, 3)
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=16)
    ap.add_argument("--per-category", type=int, default=SAMPLE_PER_CATEGORY,
                    help="cases drawn per category before the overall limit")
    ap.add_argument("--arms", default="A,B,C,D,E")
    ap.add_argument("--models", default="deepseek,gptoss,minimax27")
    ap.add_argument("--class-limit", type=int, default=70)
    ap.add_argument("--workers", type=int, default=6,
                    help="(arm, model) pairs run concurrently; each is serial")
    ap.add_argument("--tag", default="v2",
                    help="separates this run's databases, workspaces and "
                         "partials from every earlier one")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-resume", dest="resume", action="store_false")
    args = ap.parse_args()

    import observe

    partial_root = PARTIAL_ROOT_BASE / f"log2026-reextract-{args.tag}"
    wanted_arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]
    wanted_models = [m.strip() for m in args.models.split(",") if m.strip()]

    built = build_arms(args.class_limit)
    cases, single_reference_categories = select_cases(args.cases, args.per_category)

    from examples.finder.lib import bench_common as bc
    from seocho.query.strategy import PromptTemplate
    sys.path.insert(0, str(ROOT / "scripts" / "benchmarks"))
    from finder_4arm_sample import KGPromptTemplate

    system_tmpl, prompt_id, prompt_file = bc.resolve_extraction_prompt("mara")
    prompt_hash = bc.short_hash(system_tmpl)
    extraction_tmpl: PromptTemplate = KGPromptTemplate(
        system=system_tmpl,
        user="Source 10-K text to extract into the graph:\n\n{{text}}")

    run = observe.Run(OUT_ROOT, "reextract", {"decisive": {
        "arms": {a: built[a]["context"]["ontology_hash"] for a in wanted_arms},
        "models": {m: MODELS[m] for m in wanted_models},
        "cases": [c["case_id"] for c in cases],
        "single_reference_categories": single_reference_categories,
        "prompt_id": prompt_id, "prompt_hash": prompt_hash,
        "class_limit": args.class_limit, "seed": 42,
    }, "runtime": {"uri": URI, "resume": args.resume,
                   "partials": str(partial_root), "tag": args.tag}})

    total = len(wanted_arms) * len(wanted_models) * len(cases)
    references = sum(len(c["references"]) for c in cases)
    run.log(f"plan: {len(wanted_arms)} arms x {len(wanted_models)} models x "
            f"{len(cases)} cases = {total} extractions (PAID)")
    run.log(f"      {references} reference passages per arm-model, so roughly "
            f"{references * len(wanted_arms) * len(wanted_models) * 2} model "
            f"calls including the linking pass")
    run.log(f"      prompt {prompt_file} ({prompt_hash})")
    for arm in wanted_arms:
        ctx = built[arm]["context"]
        run.log(f"      arm {arm} {ctx['id']:9s} {ctx['nodes']:3d} nodes "
                f"{ctx['relationships']:3d} rels  {ctx['ontology_hash']}")

    if args.dry_run:
        for case in cases:
            run.log(f"      {case['category']:20s} {case['case_id']}  "
                    f"{case['n_refs']} refs  {case['query'][:56]}")
        run.finish({"planned": total, "dry_run": True})
        return 0

    partial_root.mkdir(parents=True, exist_ok=True)
    ensured: set[str] = set()
    ensure_lock = threading.Lock()
    records: list[dict[str, Any]] = []
    records_lock = threading.Lock()
    tally = {"done": 0, "skipped": 0, "failed": 0}

    def run_pair(arm_key: str, model_key: str) -> None:
        arm = built[arm_key]
        model = MODELS[model_key]
        database = database_for(arm_key, model_key, args.tag)
        with ensure_lock:
            if database not in ensured:
                with run.stage("db.ensure", database=database) as out:
                    out.update(ensure_database(database))
                ensured.add(database)
        for case in cases:
            partial = (partial_root /
                       f"{arm_key}_{model_key}_{case['case_id']}.json")
            if args.resume and partial.is_file():
                prior = json.loads(partial.read_text())
                if (prior.get("ontology_hash") == arm["context"]["ontology_hash"]
                        and prior.get("prompt_hash") == prompt_hash
                        and prior.get("status") == "ok"):
                    with records_lock:
                        records.append(prior)
                        tally["skipped"] += 1
                    continue
            started = time.perf_counter()
            run._append({"stage": "index.case", "status": "started",
                         "arm": arm_key, "ontology": arm["id"], "model": model,
                         "case": case["case_id"], "category": case["category"],
                         "references": len(case["references"]),
                         "database": database})
            record = index_one(run, arm, model_key, model, case,
                               extraction_tmpl, database, args.tag)
            record["prompt_hash"] = prompt_hash
            partial.write_text(json.dumps(record, indent=2,
                                          ensure_ascii=False) + "\n")
            run._append({"stage": "index.case", "status": record["status"],
                         "seconds": round(time.perf_counter() - started, 3),
                         "output": record})
            with records_lock:
                records.append(record)
                if record["status"] == "ok":
                    tally["done"] += 1
                else:
                    tally["failed"] += 1
            run.log(f"  [{arm_key}/{model_key}] {case['case_id']} "
                    f"{record['status']} nodes={record.get('nodes', 0)} "
                    f"rels={record.get('rels', 0)} "
                    f"calls={record.get('llm_calls', 0)} "
                    f"{record.get('seconds', 0)}s"
                    + (f" ERROR {record.get('error')}"
                       if record["status"] != "ok" else ""))

    pairs = [(a, m) for a in wanted_arms for m in wanted_models]
    run.log(f"running {len(pairs)} (arm, model) pairs on {args.workers} workers")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_pair, a, m): (a, m) for a, m in pairs}
        for future in as_completed(futures):
            arm_key, model_key = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 — recorded, never imputed
                run.log(f"  PAIR FAILED {arm_key}/{model_key}: {exc!r}")
                with records_lock:
                    tally["failed"] += 1

    done, skipped, failed = tally["done"], tally["skipped"], tally["failed"]

    # Per-arm rollup. Attempted and scored are reported separately so a partial
    # run can never be read as a complete one (CLAUDE.md 20.2).
    summary: dict[str, Any] = {}
    for arm_key in wanted_arms:
        rows = [r for r in records if r["arm"] == arm_key and r["status"] == "ok"]
        attempted = len([r for r in records if r["arm"] == arm_key])
        nodes = sum(r["nodes"] for r in rows)
        summary[arm_key] = {
            "ontology": built[arm_key]["id"],
            "ontology_hash": built[arm_key]["context"]["ontology_hash"],
            "attempted": attempted, "scored": len(rows),
            "nodes": nodes, "rels": sum(r["rels"] for r in rows),
            "declared_label_nodes": sum(r.get("declared_label_nodes", 0) for r in rows),
            "declared_share": (round(sum(r.get("declared_label_nodes", 0)
                                         for r in rows) / nodes, 4)
                               if nodes else 0.0),
            "period_filled": sum(r.get("period_filled", 0) for r in rows),
            "period_share": (round(sum(r.get("period_filled", 0) for r in rows)
                                   / nodes, 4) if nodes else 0.0),
        }

    payload = {
        # Versioned by the sweep tag. Both sweeps previously wrote
        # log2026.reextract.v1, so the registry saw one contract with two
        # different meanings and the pruner treated the first sweep's run as
        # superseded — which would have deleted the evidence behind a table the
        # paper still reports.
        "contract": f"log2026.reextract.{args.tag or 'v1'}",
        "question": ("Does the ontology handed to the extractor change what the "
                     "graph contains, and does it make two models name the same "
                     "fact the same way?"),
        "held_fixed": ["cases", "reference text", "prompt template", "chunking",
                       "graph store", "seed"],
        "moving": "the ontology only",
        "prompt": {"id": prompt_id, "hash": prompt_hash},
        "tag": args.tag,
        "cases": [c["case_id"] for c in cases],
        "single_reference_categories": single_reference_categories,
        "sampling": (f"{SAMPLE_PER_CATEGORY} per category, preferring "
                     f"n_refs>={MIN_REFERENCES}; categories listed in "
                     f"single_reference_categories had none and fell back"),
        "models": {m: MODELS[m] for m in wanted_models},
        "attempted": total, "scored": done + skipped, "failed": failed,
        "by_arm": summary,
        "records": records,
    }
    (run.dir / "reextract.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{'arm':4s} {'ontology':10s} {'scored':>7s} {'nodes':>8s} {'rels':>8s} "
          f"{'declared':>9s} {'period':>7s}")
    for arm_key, cell in summary.items():
        print(f"{arm_key:4s} {cell['ontology']:10s} "
              f"{cell['scored']:3d}/{cell['attempted']:<3d} {cell['nodes']:8d} "
              f"{cell['rels']:8d} {cell['declared_share']:9.3f} "
              f"{cell['period_share']:7.3f}")

    run.finish({"attempted": total, "completed": done, "resumed": skipped,
                "failed": failed, "by_arm": summary,
                "artifact": str((run.dir / "reextract.json").relative_to(ROOT))})
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
