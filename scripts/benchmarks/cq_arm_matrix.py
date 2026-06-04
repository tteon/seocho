#!/usr/bin/env python3
"""CQ x arm structural diagnosis — the schema-side, $0 (no LLM, no DB).

For each FinDER ontology sweep arm (non-ontology / small / medium / large),
compose the FIBO modules and report, per competency question, whether the arm's
vocabulary can EXPRESS it (expressible) or not (schema_impossible), plus the
arm's conformance score. This is the schema-side half of the CQ x arm matrix
(the execution-side half runs the cypher_skeletons against each arm's extracted
graph + the arbiter route — that needs a populated DB and is run separately).

Pre-registered (CLAUDE.md §19) reading: a too-large ontology is noise. This
script measures that structurally BEFORE any extraction — if `large` covers no
more CQs than `medium` while carrying more nodes, the extra modules are noise
w.r.t. the experiment's questions.

Usage:
    PYTHONPATH=src python3 scripts/benchmarks/cq_arm_matrix.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIBO = ROOT / "examples" / "finder" / "datasets" / "fibo_modules" / "compose.py"
CQS = ROOT / "examples" / "finder" / "datasets" / "competency_questions.yaml"

ARMS = {
    "non-ontology": [],
    "small": ["be", "ind"],
    "medium": ["be", "ind", "fbc", "dbt", "acc"],
    "large": ["be", "ind", "fbc", "dbt", "acc", "fnd", "sec", "mkt", "corp"],
}


def _load_compose():
    spec = importlib.util.spec_from_file_location("fibo_compose", FIBO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.compose_modules


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from seocho.ontology_governance import (  # noqa: E402
        competency_question_report,
        conformance_score,
        load_competency_questions,
    )

    compose_modules = _load_compose()
    cqs = load_competency_questions(CQS)
    ids = [c["id"] for c in cqs]

    rows = {}
    print(f"{len(cqs)} competency questions (slices S1-S6)\n")
    print("arm".ljust(13), "  ".join(i.replace("-", "") for i in ids), " | summary")
    for arm, mods in ARMS.items():
        onto = compose_modules(mods)
        rep = competency_question_report(onto, cqs)
        cf = conformance_score(onto, competency_questions=cqs, run_reasoner=False)
        verdicts = {q["id"]: q["expressible"] for q in rep["questions"]}
        rows[arm] = {
            "nodes": len(onto.nodes),
            "expressible": rep["expressible_count"],
            "total": rep["question_count"],
            "conformance": cf["score"],
            "passed": cf["passed"],
            "verdicts": verdicts,
        }
        cells = "  ".join(" E" if verdicts[i] else " ." for i in ids)
        print(arm.ljust(13), cells,
              f" | {rep['expressible_count']}/{rep['question_count']}"
              f" conf={cf['score']} nodes={len(onto.nodes)} pass={cf['passed']}")

    # Goldilocks signal: does large cover more CQs than medium?
    if rows["large"]["expressible"] == rows["medium"]["expressible"] \
            and rows["large"]["nodes"] > rows["medium"]["nodes"]:
        extra = rows["large"]["nodes"] - rows["medium"]["nodes"]
        print(f"\n[goldilocks] large carries +{extra} nodes over medium for "
              f"+0 CQ coverage -> peripheral modules are schema noise w.r.t. the CQs.")

    print("\nJSON:\n" + json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
