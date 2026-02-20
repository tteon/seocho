#!/usr/bin/env python3
"""
Practical SHACL-like rule assessment demo.

Flow:
1) Infer a rule profile from reference graph data.
2) Assess candidate graph data against that profile.
3) Emit a practical readiness report (validation + exportability).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "extraction"))

from rule_api import RuleAssessRequest, RuleInferRequest, assess_rule_profile, infer_rule_profile

logger = logging.getLogger("shacl_practical_demo")


def _load_graph(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if "graph" in payload:
        return payload["graph"]
    return payload


def _default_reference_graph() -> Dict[str, Any]:
    return {
        "nodes": [
            {"id": "1", "label": "Company", "properties": {"name": "Acme", "employees": 100}},
            {"id": "2", "label": "Company", "properties": {"name": "Beta", "employees": 80}},
            {"id": "3", "label": "Company", "properties": {"name": "Gamma", "employees": 120}},
        ],
        "relationships": [],
    }


def _default_candidate_graph() -> Dict[str, Any]:
    return {
        "nodes": [
            {"id": "a", "label": "Company", "properties": {"name": "Acme", "employees": 90}},
            {"id": "b", "label": "Company", "properties": {"name": "", "employees": "many"}},
        ],
        "relationships": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Assess practical SHACL-like rule readiness.")
    parser.add_argument("--workspace-id", default="default")
    parser.add_argument("--reference", type=Path, help="Reference graph JSON file for rule inference")
    parser.add_argument("--candidate", type=Path, help="Candidate graph JSON file for readiness assessment")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output/rules_assessment_demo.json"),
        help="Output report path",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    reference_graph = _load_graph(args.reference) if args.reference else _default_reference_graph()
    candidate_graph = _load_graph(args.candidate) if args.candidate else _default_candidate_graph()

    logger.info("Inferring rule profile from reference graph (nodes=%d)", len(reference_graph.get("nodes", [])))
    inferred = infer_rule_profile(RuleInferRequest(workspace_id=args.workspace_id, graph=reference_graph))

    logger.info("Assessing candidate graph (nodes=%d)", len(candidate_graph.get("nodes", [])))
    assessed = assess_rule_profile(
        RuleAssessRequest(
            workspace_id=args.workspace_id,
            graph=candidate_graph,
            rule_profile=inferred.rule_profile,
        )
    )

    report = {
        "workspace_id": args.workspace_id,
        "reference": {
            "node_count": len(reference_graph.get("nodes", [])),
            "rule_count": len(inferred.rule_profile.get("rules", [])),
        },
        "candidate": {
            "node_count": len(candidate_graph.get("nodes", [])),
        },
        "assessment": assessed.model_dump(),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)

    logger.info("Wrote practical readiness report to %s", args.out)
    logger.info(
        "Readiness status=%s score=%.3f",
        report["assessment"]["practical_readiness"]["status"],
        report["assessment"]["practical_readiness"]["score"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
