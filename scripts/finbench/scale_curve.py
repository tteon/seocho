#!/usr/bin/env python3
"""Workstream B6: scale curve — latency/accuracy vs graph size.

Runs the same probes across several scale factors and emits one comparable
report, so "does the middleware hold up as the graph grows?" becomes a table
instead of an intuition. Two layers are measured per scale factor:

* **graph layer** — the bounded ground-truth Cypher from verify_scenarios.py
  (no LLM): pure DB traversal cost as the graph grows.
* **agent layer** (optional, ``--models``) — the Graph Agentic RAG path per
  model: accuracy vs planted gold plus end-to-end latency.

Because the planted patterns keep the same reserved IDs at every scale factor,
the gold answers are identical across SFs — so a latency change is attributable
to graph size, not to a different question.

Usage:
    python scripts/finbench/scale_curve.py \
        --scales 1,10 --src-root outputs/finbench --db-prefix finbenchsf \
        --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" \
        --ontology examples/finbench/finbench.ontology.yaml \
        --cases examples/finbench/cases.json \
        --models gpt-oss-120b \
        --out outputs/finbench/scale_curve.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


verify_mod = _load("finbench_verify", "verify_scenarios.py")
breakdown_mod = _load("finbench_breakdown", "mara_breakdown.py")


def _markdown(report: dict) -> str:
    names = [s["name"] for s in report["scales"][0]["graph"]["scenarios"]]
    lines = ["# FinBench scale curve", "",
             "## Graph layer — tuned plan (label-qualified + indexed property)", "",
             "| SF | nodes | transfers | " + " | ".join(names) + " |",
             "|---|---|---|" + "---|" * len(names)]
    for entry in report["scales"]:
        g = entry["graph"]
        cells = [f"{s['tuned']['latency_ms']:.0f}ms / {s['tuned']['db_hits']} hits"
                 + ("" if s["passed"] else " ✗") for s in g["scenarios"]]
        lines.append(f"| {entry['scale_factor']} | {g['graph']['nodes']} | "
                     f"{g['graph']['transfers']} | " + " | ".join(cells) + " |")

    lines += ["", "## Graph layer — naive plan (unlabeled match, index unusable)", "",
              "| SF | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for entry in report["scales"]:
        cells = [f"{s['naive']['latency_ms']:.0f}ms / {s['naive']['db_hits']} hits"
                 for s in entry["graph"]["scenarios"]]
        lines.append(f"| {entry['scale_factor']} | " + " | ".join(cells) + " |")

    lines += ["", "## Cost of the wrong plan shape (naive dbHits / tuned dbHits)", "",
              "Both shapes return the same answer, so accuracy cannot distinguish them.", "",
              "| SF | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for entry in report["scales"]:
        cells = [f"{s['naive_cost_multiple']}x" for s in entry["graph"]["scenarios"]]
        lines.append(f"| {entry['scale_factor']} | " + " | ".join(cells) + " |")

    if any(entry.get("agent") for entry in report["scales"]):
        lines += ["", "## Agent layer (Graph Agentic RAG)", "",
                  "| SF | model | accuracy | p50 ms | p95 ms | errors |",
                  "|---|---|---|---|---|---|"]
        for entry in report["scales"]:
            for m in entry.get("agent") or []:
                lat = m["latency_ms"]
                lines.append(f"| {entry['scale_factor']} | {m['model']} | {m['accuracy']:.0%} | "
                             f"{lat['p50']:.0f} | {lat['p95']:.0f} | {m['errors']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", default="1,10", help="comma-separated scale factors")
    parser.add_argument("--src-root", type=Path, default=Path("outputs/finbench"))
    parser.add_argument("--db-prefix", default="finbenchsf")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="")
    parser.add_argument("--ontology", type=Path)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--models", default="", help="comma-separated MARA models (empty = graph layer only)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    scales = [int(s) for s in args.scales.split(",") if s.strip()]
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    ontology = cases = None
    if models:
        if not (args.ontology and args.cases):
            raise SystemExit("--models requires --ontology and --cases")
        from seocho.ontology import Ontology
        ontology = Ontology.load(args.ontology)
        with args.cases.open('r', encoding='utf-8') as f:
            cases = json.load(f)["cases"]

    entries = []
    for sf in scales:
        src = args.src_root / f"sf{sf}"
        database = f"{args.db_prefix}{sf}"
        print(f"[scale-curve] SF{sf} graph layer ({database}) ...", flush=True)
        graph = verify_mod.verify(src, args.uri, args.user, args.password, database)
        entry = {"scale_factor": sf, "database": database, "graph": graph}
        if models:
            agent = []
            for model in models:
                print(f"[scale-curve] SF{sf} agent layer: {model} ...", flush=True)
                agent.append(breakdown_mod._run_model(
                    model, ontology, args.uri, args.user, args.password, database, cases, True))
            entry["agent"] = agent
        entries.append(entry)

    report = {"schema_version": "seocho.finbench.scale-curve.v1", "scales": entries}
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
        args.out.with_suffix(".md").write_text(_markdown(report))
    print(_markdown(report))


if __name__ == "__main__":
    main()
