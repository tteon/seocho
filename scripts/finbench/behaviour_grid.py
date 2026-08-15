#!/usr/bin/env python3
"""How does the agent's retrieval behaviour change as the graph grows?

This is the experiment the whole project's thesis rests on, and until now it did not exist.
Almost everything measured here was measured at a single scale factor: plan-shape cost has a
curve, but *agent behaviour* — what it asks for, how expensively, how honestly — was one
point. The one figure ever quoted for behaviour against scale (100% accuracy at SF1 falling
to 67% at SF1000) predates a direction bug that was fixed since, so it cannot be trusted.

Two axes, crossed:

* **Scale** — SF1 through SF1000, a thousandfold in volume.
* **Distribution** — uniform attachment against a power-law graph with triadic and cyclic
  closure. Held separate because volume with the shape fixed changed nothing measurable,
  while shape at fixed volume turned a 45 ms aggregate into a timeout.

**Questions are matched by quantile, not by anchor id or absolute cost.** Anchor ids are
snapshot-specific, and absolute L2 bands do not exist at small scale — SF1 has 10,000 edges
in total, so there is no anchor with the L2 of 192,942 that SF1000's p99.9 band carries.
Holding the *quantile* fixed makes "the p99 question at this scale" the comparable unit and
lets its absolute difficulty grow with the graph, which is what "as it grows" means.

Five families of measurement, because accuracy alone was repeatedly shown to be blind to what
actually degrades:

``cost``          db hits per answer, sargable rate
``shape``         share of answers reached by a query that can stop early
``discrimination`` precision separated from recall — distractors grow with the graph while
                  recall does not notice
``honesty``       when the true answer exceeds the guardrail's row cap, does the answer say
                  so? Nothing currently checks this, and the largest case in the set has a
                  true answer of 908,649 accounts
``context``       input tokens per question. The row cap means context should *not* grow with
                  scale; if it stays flat while accuracy falls, the problem is discrimination
                  rather than context volume, and that is a different fix

Usage:
    python scripts/finbench/behaviour_grid.py --scales 1,10,100,1000 \
        --distributions unif,real --model gpt-oss-120b --password "$NEO4J_PASSWORD"
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent

# Quantile bands to draw questions from. Deliberately excludes the extremes: `tiny` sits
# where fixed per-query cost dominates and the cost model is uncalibrated, and `max` does not
# exist as a stable band at small scale.
BANDS = "small,medium,large"

# Phrases an answer can use to disclose that it is partial. Kept explicit rather than asking
# a model to judge, so the metric is reproducible — and kept generous, because the failure
# mode being measured is saying *nothing*, not choosing the wrong words.
DISCLOSURE = re.compile(
    r"\b(up to|at most|first|only|partial|truncat|sample|subset|limited to|showing|"
    r"more than|exceeds|among|some of)\b", re.I)


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _run(cmd: List[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def prepare_point(src: Path, database: str, password: str, per_band: int,
                  py: str) -> Optional[Path]:
    """Curate anchors and build a case file for one grid point.

    Curation runs per snapshot because anchor ids do not carry across snapshots — pricing one
    snapshot's anchors against another's cost model produced an entirely meaningless first run
    of the workload gate, and the case files now record `curated_from` so that cannot recur.
    """
    cur = _run([py, str(_HERE / "curate_parameters.py"), "--src", str(src),
                "--per-band", str(per_band)])
    if cur.returncode != 0:
        print(f"[grid] curate failed for {src}: {cur.stderr[-300:]}", flush=True)
        return None
    cases = src / "cases_grid.json"
    gen = _run([py, str(_HERE / "hub_cases.py"), "--src", str(src),
                "--bands", BANDS, "--out", str(cases)])
    if gen.returncode != 0 or not cases.exists():
        print(f"[grid] cases failed for {src}: {gen.stderr[-300:]}", flush=True)
        return None
    return cases


def score(report: Dict[str, Any], cases: List[Dict[str, Any]],
          row_cap: int) -> Dict[str, Any]:
    """Turn one agent run into behaviour metrics.

    Accuracy is reported but is not the point: it was blind to a plan that did 264,005x the
    work for the same answer, and blind to a bounded answer presented as a complete one.
    """
    models = report.get("models") or []
    if not models:
        return {"error": "no model result"}
    m = models[0]
    stages = m.get("stages") or {}
    by_id = {c.get("id"): c for c in cases}
    rows = m.get("cases") or []

    terminable_emitted = 0
    over_cap = 0
    over_cap_disclosed = 0
    tokens: List[int] = []
    exact = 0
    superset = 0
    scored_sets = 0

    for r in rows:
        case = by_id.get(r.get("id"), {})
        diff = case.get("difficulty") or {}
        cypher = (r.get("cypher") or "").lower()
        answer = str(r.get("answer") or "")

        if "limit" in cypher:
            terminable_emitted += 1

        # Honesty: only meaningful where the true answer cannot fit under the cap.
        if diff.get("answer_size", 0) > row_cap:
            over_cap += 1
            if DISCLOSURE.search(answer):
                over_cap_disclosed += 1

        tu = r.get("token_usage") or {}
        if tu.get("input_tokens_est") is not None:
            tokens.append(int(tu["input_tokens_est"]))

        # Precision separated from recall. Only set-valued answers can be superset-wrong;
        # a scalar count is either right or not.
        gold = case.get("gold") or []
        if len(gold) > 1:
            scored_sets += 1
            found = set(re.findall(r"\d+", answer))
            want = set(gold)
            if want and want <= found:
                if found - want:
                    superset += 1
                else:
                    exact += 1

    n = len(rows) or 1
    return {
        "cases": len(rows),
        "accuracy": m.get("accuracy"),
        "sargable_rate": stages.get("s4_sargable_rate"),
        "db_hits_total": stages.get("s4_db_hits_total"),
        "db_hits_per_answer": (round(stages["s4_db_hits_total"] / n, 1)
                               if stages.get("s4_db_hits_total") is not None else None),
        "terminable_emitted_rate": round(terminable_emitted / n, 4),
        "answers_over_row_cap": over_cap,
        "over_cap_disclosed": over_cap_disclosed,
        "disclosure_rate": (round(over_cap_disclosed / over_cap, 4) if over_cap else None),
        "set_answers_scored": scored_sets,
        "exact_sets": exact,
        "superset_sets": superset,
        "mean_input_tokens": round(sum(tokens) / len(tokens), 1) if tokens else None,
        "engine_ms_total": stages.get("engine_ms_total"),
        "llm_ms_total": stages.get("llm_ms_total"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", default="1,10,100,1000")
    parser.add_argument("--distributions", default="unif,real")
    parser.add_argument("--root", type=Path, default=Path("outputs/finbench"))
    parser.add_argument("--ontology", type=Path,
                        default=Path("examples/finbench/finbench.ontology.yaml"))
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--password", default="")
    parser.add_argument("--per-band", type=int, default=3)
    parser.add_argument("--row-cap", type=int, default=50)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--skip-prepare", action="store_true",
                        help="reuse cases_grid.json already present under each snapshot")
    parser.add_argument("--out", type=Path,
                        default=Path("outputs/finbench/behaviour_grid.json"))
    args = parser.parse_args()

    scales = [int(s) for s in args.scales.split(",") if s.strip()]
    dists = [d.strip() for d in args.distributions.split(",") if d.strip()]

    points: List[Dict[str, Any]] = []
    for sf in scales:
        for dist in dists:
            src = args.root / f"sf{sf}-{dist}"
            database = f"finbenchsf{sf}{dist}"
            if not src.exists():
                print(f"[grid] {src} missing, skipping", flush=True)
                continue

            cases_path = src / "cases_grid.json"
            if not args.skip_prepare or not cases_path.exists():
                cases_path = prepare_point(src, database, args.password,
                                           args.per_band, args.python)
                if cases_path is None:
                    continue

            cases = json.loads(cases_path.read_text())["cases"]
            out_json = src / f"grid_{args.model}.json"
            run = _run([args.python, str(_HERE / "mara_breakdown.py"),
                        "--ontology", str(args.ontology), "--cases", str(cases_path),
                        "--database", database, "--password", args.password,
                        "--models", args.model, "--reasoning-mode",
                        "--out", str(out_json)])
            if not out_json.exists():
                print(f"[grid] sf{sf}-{dist} agent run produced nothing: "
                      f"{run.stderr[-300:]}", flush=True)
                continue

            report = json.loads(out_json.read_text())
            metrics = score(report, cases, args.row_cap)
            manifest = json.loads((src / "manifest.json").read_text())
            sp = manifest.get("structural_profile") or {}
            dp = manifest.get("degree_profile") or {}
            points.append({
                "sf": sf, "distribution": dist, "database": database,
                "structure": {
                    "max_degree": dp.get("max"),
                    "avg_local_clustering": (sp.get("clustering") or {})
                        .get("avg_local_clustering"),
                    "directed_3_cycles": (sp.get("motifs") or {}).get("directed_3_cycles"),
                    "edges": (sp.get("multiplicity") or {}).get("edges"),
                },
                **metrics,
            })
            print(f"[grid] sf{sf:<5} {dist:5s} acc={metrics.get('accuracy')} "
                  f"sarg={metrics.get('sargable_rate')} "
                  f"hits/ans={metrics.get('db_hits_per_answer')} "
                  f"term={metrics.get('terminable_emitted_rate')} "
                  f"disclose={metrics.get('disclosure_rate')} "
                  f"tokens={metrics.get('mean_input_tokens')}", flush=True)

    report = {"schema_version": "seocho.finbench.behaviour-grid.v1",
              "model": args.model, "row_cap": args.row_cap,
              "bands": BANDS, "points": points}

    lines = ["# Agent behaviour against scale and distribution", "",
             f"model `{args.model}` · questions matched by L2 quantile band "
             f"(`{BANDS}`) · guardrail row cap {args.row_cap}", "",
             "Questions are quantile-matched rather than anchor-matched: anchor ids do not "
             "carry across snapshots, and SF1 has no anchor with SF1000's absolute L2. "
             "Holding the quantile fixed lets the question's absolute difficulty grow with "
             "the graph, which is what scaling means here.", "",
             "| SF | dist | max degree | clustering | accuracy | sargable | db hits/answer | "
             "terminable emitted | over cap | disclosed | input tokens |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in points:
        st = p["structure"]
        def f(v, spec=""):
            return "—" if v is None else format(v, spec) if spec else str(v)
        lines.append(
            f"| {p['sf']} | {p['distribution']} | {f(st['max_degree'], ',')} | "
            f"{f(st['avg_local_clustering'], '.4f')} | {f(p.get('accuracy'), '.0%')} | "
            f"{f(p.get('sargable_rate'), '.0%')} | {f(p.get('db_hits_per_answer'), ',.0f')} | "
            f"{f(p.get('terminable_emitted_rate'), '.0%')} | "
            f"{p.get('answers_over_row_cap')} | {f(p.get('disclosure_rate'), '.0%')} | "
            f"{f(p.get('mean_input_tokens'), ',.0f')} |")
    markdown = "\n".join(lines) + "\n"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    args.out.with_suffix(".md").write_text(markdown)
    print(markdown)


if __name__ == "__main__":
    main()
