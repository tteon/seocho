#!/usr/bin/env python3
"""Bootstrap unannotated SEOCHO case envelopes from a GraphRAG-Bench ledger.

The input ledger contains only upstream references. This tool intentionally
does not load or copy questions, answers, rationale text, textbook passages,
triples, or Cypher. Reviewers add those local labels later in a separately
controlled artifact.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from seocho.eval.case_envelope import CASE_ENVELOPE_SCHEMA_VERSION


def load_ledger(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected an object")
        rows.append(value)
    return rows


def envelope_from_ledger_row(row: dict[str, Any]) -> dict[str, Any]:
    upstream = row.get("upstream", {})
    if not isinstance(upstream, dict) or not isinstance(upstream.get("source_sha256"), str):
        raise ValueError("ledger row lacks upstream.source_sha256")
    case_id = row.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("ledger row lacks case_id")
    return {
        "schema_version": CASE_ENVELOPE_SCHEMA_VERSION,
        "case_id": case_id,
        "source": {
            "snapshot_sha256": upstream["source_sha256"],
            "document_refs": [],
            "upstream_repository": upstream.get("repository"),
            "upstream_source_file": upstream.get("source_file"),
        },
        "layers": {
            "ontology": {"status": "unannotated"},
            "triples": {"status": "unannotated"},
            "query": {"status": "unannotated"},
            # The official answer stays in the upstream dataset/evaluator; the
            # reference ledger deliberately never copies it into this artifact.
            "answer": {"status": "unavailable", "reason": "upstream_answer_not_copied"},
            "governance": {"status": "unannotated"},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    envelopes = [envelope_from_ledger_row(row) for row in load_ledger(args.ledger)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in envelopes),
        encoding="utf-8",
    )
    print(json.dumps({"case_count": len(envelopes), "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
