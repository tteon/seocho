#!/usr/bin/env python3
"""Workstream B7: per-model Graph Agentic RAG breakdown over MARA models.

For each MARA-served model, build a local Seocho query client (FinBench ontology
+ the loaded DozerDB graph + that model as the LLM), ask every showcase scenario
(examples/finbench/cases.json), and break the model down on: accuracy vs planted
gold, latency (avg / p95), and failure/invalid-query count. This measures Track
7's thesis — the graph/ontology are durable, the LLM is swappable compute — as a
number, not a hope.

Requires MARA_API_KEY in the environment.

Usage:
    python scripts/finbench/mara_breakdown.py \
        --ontology examples/finbench/finbench.ontology.yaml \
        --cases examples/finbench/cases.json \
        --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" \
        --database finbenchsf1 \
        --models DeepSeek-V3.1,MiniMax-M2.5,gpt-oss-120b \
        --out outputs/finbench/sf1/mara_breakdown.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "finbench_instrumentation", Path(__file__).resolve().parent / "instrumentation.py")
instrumentation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(instrumentation)


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(round((len(ordered) - 1) * p)), len(ordered) - 1)]


def _scored(answer: str, gold: list[str]) -> bool:
    """An answer is correct when every gold token appears.

    A token may offer alternatives with ``|`` (e.g. ``"WIRE_CROSSBORDER|국외 송금"``):
    a channel is equally correctly named by its code or its human label, and
    penalizing the label would measure surface form rather than graph reasoning.
    """
    low = (answer or "").lower()
    return all(
        any(alt.strip().lower() in low for alt in str(tok).split("|") if alt.strip())
        for tok in gold
    )


def _run_model(model: str, ontology, uri: str, user: str, password: str,
               database: str, cases: list[dict], reasoning_mode: bool) -> dict:
    from seocho.client import Seocho
    from seocho.store.graph import Neo4jGraphStore
    from seocho.store.llm import create_llm_backend

    store = Neo4jGraphStore(uri, user, password)
    client = Seocho(
        ontology=ontology,
        graph_store=store,
        llm=create_llm_backend(provider="mara", model=model),
        workspace_id="default",
    )

    results: list[dict] = []
    for case in cases:
        start = time.perf_counter()
        answer, metadata, error = "", {}, None
        try:
            resp = client.ask_response(
                case["question"], database=database, reasoning_mode=reasoning_mode,
                repair_budget=1, query_mode="graph_cot",
            )
            answer = str(getattr(resp, "response", resp) or "")
            metadata = dict(getattr(client, "last_query_metadata", {}) or {})
        except Exception as exc:  # invalid query / model / transport failure
            error = f"{type(exc).__name__}: {exc}"[:300]
        latency_ms = (time.perf_counter() - start) * 1000.0

        # Stage-wise fingerprint (S1..S5). Plan quality is probed by re-running the
        # generated query under PROFILE, so sargability is measured, not guessed.
        plan = None
        if metadata.get("cypher"):
            plan = instrumentation.profile_cypher(
                getattr(store, "_driver", None) or getattr(store, "driver", None),
                metadata["cypher"], metadata.get("params") or {}, database,
            )
        row = instrumentation.fingerprint(
            case=case, answer=answer, metadata=metadata,
            orientation_repair=instrumentation.detect_orientation_repair(
                ontology, metadata.get("intent_data")),
            plan=plan, error=error, latency_ms=latency_ms,
        )
        results.append(row)

    lat = [r["latency_ms"] for r in results]
    correct = sum(1 for r in results if r["correct"])
    errors = sum(1 for r in results if r["error"])
    return {
        "model": model,
        "accuracy": correct / len(results) if results else 0.0,
        "correct": correct, "total": len(results), "errors": errors,
        "latency_ms": {"avg": sum(lat) / len(lat) if lat else 0.0,
                       "p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95)},
        "stages": instrumentation.aggregate(results),
        "cases": results,
    }


def _markdown(report: dict) -> str:
    lines = ["# FinBench MARA model breakdown", "",
             f"database: `{report['database']}` · reasoning_mode: {report['reasoning_mode']}", "",
             "| model | accuracy | correct/total | errors | p50 ms | p95 ms |",
             "|---|---|---|---|---|---|"]
    for m in report["models"]:
        lat = m["latency_ms"]
        lines.append(f"| {m['model']} | {m['accuracy']:.0%} | {m['correct']}/{m['total']} | "
                     f"{m['errors']} | {lat['p50']:.0f} | {lat['p95']:.0f} |")
    lines += ["", "## Stage-wise breakdown (where agents differ)", "",
              "| model | S2 slot-fill | S3 supported | S4 sargable | S5 accuracy | S5 exact | superset | guardrail repairs | LLM ms | engine ms |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    def _pctf(v):
        return "n/a" if v is None else f"{v:.0%}"
    for m in report["models"]:
        st = m.get("stages") or {}
        lines.append(
            f"| {m['model']} | {_pctf(st.get('s2_slot_fill_rate'))} | {_pctf(st.get('s3_supported_rate'))} | "
            f"{_pctf(st.get('s4_sargable_rate'))} | {_pctf(st.get('s5_accuracy'))} | {_pctf(st.get('s5_exact_rate'))} | "
            f"{_pctf(st.get('s5_superset_rate'))} | {_pctf(st.get('guardrail_repair_rate'))} | "
            f"{st.get('llm_ms_total', 0):.0f} | {st.get('engine_ms_total', 0):.0f} |")
    lines += ["", "## Per-scenario correctness", "",
              "| scenario | " + " | ".join(m["model"] for m in report["models"]) + " |",
              "|---|" + "---|" * len(report["models"])]
    ids = [c["id"] for c in report["models"][0]["cases"]] if report["models"] else []
    for cid in ids:
        cells = []
        for m in report["models"]:
            row = next((c for c in m["cases"] if c["id"] == cid), None)
            cells.append("✓" if row and row["correct"] else ("⚠" if row and row["error"] else "✗"))
        lines.append(f"| {cid} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ontology", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", required=True)
    parser.add_argument("--models", default="DeepSeek-V3.1,MiniMax-M2.5,gpt-oss-120b")
    parser.add_argument("--reasoning-mode", action="store_true", default=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    from seocho.ontology import Ontology
    ontology = Ontology.load(args.ontology)
    with args.cases.open('r', encoding='utf-8') as f:
        cases = json.load(f)["cases"]
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    model_reports = []
    for model in models:
        print(f"[mara-breakdown] running {model} over {len(cases)} cases ...", flush=True)
        model_reports.append(
            _run_model(model, ontology, args.uri, args.user, args.password,
                       args.database, cases, args.reasoning_mode))

    report = {
        "schema_version": "seocho.finbench.mara-breakdown.v1",
        "database": args.database,
        "reasoning_mode": args.reasoning_mode,
        "models": model_reports,
    }
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
        args.out.with_suffix(".md").write_text(_markdown(report))
    print(_markdown(report))


if __name__ == "__main__":
    main()
