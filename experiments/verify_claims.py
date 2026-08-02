#!/usr/bin/env python3
"""Check that every claim the paper wants to make has code, data, and a trace.

A claim in a paper needs three things behind it and it is easy to have only two:
a script that can be rerun, an artifact holding the numbers, and a trace showing
what actually happened when it ran. This walks the claim list and reports which
of the three each one has, so "we verified that" can be checked rather than
believed.

    python3 experiments/verify_claims.py           report
    python3 experiments/verify_claims.py --strict  exit non-zero if any claim
                                                   is missing code or data

Columns:
    code       the script exists and parses
    data       an artifact with the declared contract exists
    traced     the run directory holds trace.jsonl, so the steps are auditable
    otel       the run directory holds spans.jsonl, so the same steps are in
               OpenTelemetry form and can go to a collector
    driver     the run directory holds driver.jsonl, meaning the database side
               was observed at the driver rather than only at our wrapper

A claim with data but no trace was measured before the harness existed. It is
not wrong, but it cannot be audited step by step, and that is worth knowing
before it is cited.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# section -> claims. Each claim names the script that produces it and the
# contract of the artifact that holds it. Both are checked against the disk.
CLAIMS: list[dict[str, Any]] = [
    # --- Section 1.0  isolation -------------------------------------------
    {"section": "1.0 isolation", "sector": "corpus",
     "claim": "mixing categories would merge names that mean different things",
     "script": "experiments/minimal/category_contamination.py",
     "contract": "log2026.category_contamination.v1"},

    # --- Section 1.1  the problem ------------------------------------------
    {"section": "1.1 problem", "sector": "model",
     "claim": "models invent different identifiers for the same document",
     "script": None, "contract": "log2026.merge_key_reality.v1"},
    {"section": "1.1 problem", "sector": "model",
     "claim": "a single model does not agree with its own second run either",
     "script": "experiments/minimal/self_consistency.py",
     "contract": "log2026.self_consistency.v1"},
    {"section": "1.1 problem", "sector": "model",
     "claim": "models differ in what they capture, not only in what they call it",
     "script": "experiments/minimal/fact_recall.py",
     "contract": "log2026.fact_recall.v1"},

    # --- Section 1.2  is the ontology fit ----------------------------------
    {"section": "1.2 ontology fit", "sector": "ontology",
     "claim": "FIBO can name what these questions are about",
     "script": "experiments/minimal/ontology_task_fit.py",
     "contract": "log2026.ontology_task_fit.v1"},
    {"section": "1.2 ontology fit", "sector": "ontology",
     "claim": "FIBO asks the same kind of question the corpus asks",
     "script": "experiments/minimal/cq_similarity.py",
     "contract": "log2026.cq_similarity.v1"},
    {"section": "1.2 ontology fit", "sector": "ontology",
     "claim": "FIBO's formal labels are not the words the filings use",
     "script": "experiments/minimal/alias_register.py",
     "contract": "log2026.alias_register.v2"},
    {"section": "1.2 ontology fit", "sector": "ontology",
     "claim": "the competency questions run as queries, not prose",
     "script": "experiments/minimal/cq_suite.py",
     "contract": "log2026.cq_suite.v1"},

    # --- Section 1.3  what the axioms add ----------------------------------
    {"section": "1.3 axioms", "sector": "ontology",
     "claim": "reasoning derives structure the flat class list lacks",
     "script": "experiments/minimal/reasoner_pretest.py",
     "contract": "log2026.reasoner_pretest.v2"},
    {"section": "1.3 axioms", "sector": "ontology",
     "claim": "a complete reasoner confirms the lower bound",
     "script": None, "contract": "log2026.reasoner_pretest.v3"},
    {"section": "1.3 axioms", "sector": "ontology",
     "claim": "a real constraint checker catches violations the string check misses",
     "script": "experiments/minimal/shacl_check.py",
     "contract": "log2026.shacl_check.v1"},

    # --- Section 1.4  the result -------------------------------------------
    {"section": "1.4 result", "sector": "ontology x model",
     "claim": "which schema makes two models agree (first pass)",
     "script": "experiments/minimal/arm_results.py",
     "contract": "log2026.arm_results.v1"},
    {"section": "1.4 result", "sector": "ontology x model",
     "claim": "same comparison with property slots equalized, hierarchy added",
     "script": "experiments/minimal/reextract.py",
     "contract": "log2026.reextract.v2"},
    {"section": "1.4 result", "sector": "measurement",
     "claim": "the differences between schemas are larger than sampling noise",
     "script": "experiments/minimal/uncertainty.py",
     "contract": "log2026.arm_uncertainty.v1"},
    {"section": "1.4 result", "sector": "measurement",
     "claim": "the extractor actually received and used the schema it was given",
     "script": "experiments/minimal/manipulation_check.py",
     "contract": "log2026.manipulation_check.v1"},
    {"section": "1.4 result", "sector": "measurement",
     "claim": "no case fell back to the heuristic and contaminated a schema",
     "script": "experiments/minimal/arm_results.py",
     "contract": "log2026.arm_results.v1"},
    {"section": "1.4 result", "sector": "correctness",
     "claim": "the graph contains the answer the dataset says is right",
     "script": "experiments/minimal/gold_coverage.py",
     "contract": "log2026.gold_coverage.v1"},

    # --- Section 1.5  the mechanism ----------------------------------------
    {"section": "1.5 mechanism", "sector": "ontology",
     "claim": "declaring a type changes how findable the thing is across views",
     "script": "experiments/minimal/type_findability.py",
     "contract": "log2026.type_findability.v1"},
    {"section": "1.5 mechanism", "sector": "ontology",
     "claim": "synonyms collapse two surface forms onto one node",
     "script": "experiments/minimal/alias_collapse.py",
     "contract": "log2026.alias_collapse.v1"},
    {"section": "1.5 mechanism", "sector": "ontology",
     "claim": "it is the number of classes, not FIBO's classes, that fragments names",
     "script": "experiments/minimal/class_count_control.py",
     "contract": "log2026.class_count_control.v1"},
    # --- Section 2.1  the schema an agent queries with ---------------------
    {"section": "2.1 query schema", "sector": "query",
     "claim": "the introspected schema and the declared one differ materially",
     "script": "experiments/schema_sources.py",
     "contract": "log2026.schema_sources.v1"},
    {"section": "2.1 query schema", "sector": "query",
     "claim": "which obstacles to querying a prompt can remove and which it cannot",
     "script": "experiments/schema_legibility.py",
     "contract": "log2026.schema_legibility.v1"},
    {"section": "2.1 query schema", "sector": "query",
     "claim": "the graphs are loaded, isolated by category, with provenance",
     "script": "experiments/load_categories.py",
     "contract": "log2026.category_load.v1"},
    {"section": "2.1 query schema", "sector": "query",
     "claim": "a query-writing agent fails less under the declared description",
     "script": "experiments/query_agent.py",
     "contract": "log2026.query_agent.v1"},

    # --- Section 2.2  did the agent use what it was given -------------------
    {"section": "2.2 utilisation", "sector": "query",
     "claim": "the query changes at all when the description changes",
     "script": "experiments/query_agent.py",
     "contract": "log2026.query_diff.v1"},
    {"section": "2.2 utilisation", "sector": "query",
     "claim": "the agent used the schema it was given, traceable in the query",
     "script": "experiments/query_utilisation.py",
     "contract": "log2026.query_utilisation.v1"},
    {"section": "2.2 utilisation", "sector": "query",
     "claim": "grounding, selection and composition fail separately",
     "script": "experiments/query_utilisation.py",
     "contract": "log2026.query_stages.v1"},
    {"section": "2.2 utilisation", "sector": "correctness",
     "claim": "questions are built from the graph, answerable and unanswerable",
     "script": "experiments/question_set.py",
     "contract": "log2026.question_set.v1"},

    {"section": "1.5 mechanism", "sector": "measurement",
     "claim": "the finding survives a different definition of 'the same fact'",
     "script": "experiments/minimal/key_rule_sensitivity.py",
     "contract": "log2026.key_rule_sensitivity.v1"},
]


def find_artifact(contract: str) -> Path | None:
    index = ROOT / "experiments/results_index.json"
    if index.is_file():
        payload = json.loads(index.read_text())
        matches = [r for r in payload.get("results", [])
                   if r.get("contract") == contract]
        if matches:
            newest = max(matches, key=lambda r: r["modified"])
            return ROOT / newest["path"]
    for path in (ROOT / "outputs").rglob("*.json"):
        try:
            if json.loads(path.read_text()).get("contract") == contract:
                return path
        except Exception:  # noqa: BLE001
            continue
    return None


def check(claim: dict[str, Any]) -> dict[str, Any]:
    row = dict(claim)
    script = claim.get("script")
    if script is None:
        row["code"] = "inline"
    else:
        path = ROOT / script
        if not path.is_file():
            row["code"] = "missing"
        else:
            try:
                ast.parse(path.read_text())
                row["code"] = "ok"
            except SyntaxError:
                row["code"] = "broken"
    artifact = find_artifact(claim["contract"])
    row["artifact"] = str(artifact.relative_to(ROOT)) if artifact else ""
    row["data"] = "ok" if artifact else "missing"
    if artifact:
        run_dir = artifact.parent
        row["traced"] = (run_dir / "trace.jsonl").is_file()
        row["otel"] = (run_dir / "spans.jsonl").is_file()
        row["driver"] = (run_dir / "driver.jsonl").is_file()
    else:
        row["traced"] = row["otel"] = row["driver"] = False
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    rows = [check(c) for c in CLAIMS]

    mark = {True: "yes", False: "-"}
    section = ""
    print(f"{'claim':62s} {'code':8s} {'data':8s} {'trace':6s} {'otel':5s} {'db':4s}")
    for row in rows:
        if row["section"] != section:
            section = row["section"]
            print(f"\n[{section}]  sector: "
                  f"{', '.join(sorted({r['sector'] for r in rows if r['section'] == section}))}")
        print(f"  {row['claim'][:60]:60s} {row['code']:8s} {row['data']:8s} "
              f"{mark[row['traced']]:6s} {mark[row['otel']]:5s} {mark[row['driver']]:4s}")

    have = sum(1 for r in rows if r["data"] == "ok")
    traced = sum(1 for r in rows if r["traced"])
    print(f"\n{have}/{len(rows)} claims have data; {traced} of those are traced")

    by_sector: dict[str, list[dict]] = {}
    for row in rows:
        by_sector.setdefault(row["sector"], []).append(row)
    print("\nby sector:")
    for sector, group in sorted(by_sector.items()):
        done = sum(1 for r in group if r["data"] == "ok")
        print(f"  {sector:18s} {done}/{len(group)}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "contract": "seocho.claim_audit.v1",
            "question": "Does every claim have code, data and a trace behind it?",
            "claim_boundary": ("Checks existence, not correctness. A claim can "
                               "have all three and still be wrong."),
            "claims": rows}, indent=2) + "\n")
        print(f"\nwrote {args.json}")

    if args.strict:
        broken = [r for r in rows if r["code"] in ("missing", "broken")
                  or r["data"] == "missing"]
        return 1 if broken else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
