#!/usr/bin/env python3
"""Validate local evaluation-case JSONL and emit content-free coverage receipts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from seocho.eval.case_envelope import annotation_coverage, case_receipt


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{line_number}: expected an object")
        cases.append(item)
    return cases


def build_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    receipts = [case_receipt(case) for case in cases]
    return {
        "schema_version": "seocho.evaluation_case_envelope_report.v1",
        "annotation_coverage": annotation_coverage(cases),
        "receipts": receipts,
        "valid": all(receipt["valid"] for receipt in receipts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Local case-envelope JSONL")
    parser.add_argument("--output", type=Path, required=True, help="Content-free report JSON")
    args = parser.parse_args()
    report = build_report(load_cases(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"valid": report["valid"], **report["annotation_coverage"]}, indent=2, sort_keys=True))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
