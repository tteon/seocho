#!/usr/bin/env python3
"""Create a controlled 24-case gold fixture for governance-pipeline calibration.

This is intentionally *not* an external semantic benchmark. It provides fully
known graph facts, result identifiers, and negative governance variants so the
evaluation machinery can be exercised before reviewed external annotations are
available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from seocho.eval.case_envelope import CASE_ENVELOPE_SCHEMA_VERSION


_WORKSPACES = ("fixture-tenant-a", "fixture-tenant-b")
_QUERY_TEMPLATES = (
    ("ordered_steps", ("step", "sequence"), "List the ordered memory steps for this exchange transaction."),
    ("event_count", ("event_count",), "How many memory events belong to this exchange transaction?"),
    ("event_provenance", ("provenance",), "Show the provenance for this exchange transaction's memory events."),
)
_VARIANTS = (
    ("valid", True, "none"),
    ("stale_bundle", False, "stale_active_bundle"),
    ("unreceipted_candidate", False, "missing_candidate_receipt"),
    ("cross_workspace", False, "workspace_mismatch"),
)


def _source_digest() -> str:
    payload = {"fixture": "governed-memory-calibration.v1", "events_per_intent": 3}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def build_cases() -> list[dict[str, Any]]:
    """Return 2 workspaces × 3 query forms × 4 admission variants."""
    rows: list[dict[str, Any]] = []
    source_digest = _source_digest()
    for workspace in _WORKSPACES:
        for query_id, required_slots, question in _QUERY_TEMPLATES:
            for variant_id, admit, failure_class in _VARIANTS:
                expected_ids = (
                    [f"{workspace}:event:{number}" for number in range(1, 4)]
                    if query_id != "event_count"
                    else [f"{workspace}:event-count:3"]
                )
                rows.append(
                    {
                        "schema_version": CASE_ENVELOPE_SCHEMA_VERSION,
                        "case_id": f"governed-memory:{workspace}:{query_id}:{variant_id}",
                        "source": {
                            "snapshot_sha256": source_digest,
                            "document_refs": ["fixture:governed-memory-calibration.v1"],
                        },
                        # Local-only raw question is present solely so a live
                        # runner can replay this controlled fixture.
                        "question": question,
                        "layers": {
                            "ontology": {
                                "status": "reviewed",
                                "required_terms": ["ExchangeIntent", "ExchangeMemoryEvent", "HAS_EVENT"],
                            },
                            "triples": {
                                "status": "reviewed",
                                "gold_triples": [
                                    {
                                        "source": f"{workspace}:intent",
                                        "relation": "HAS_EVENT",
                                        "target": f"{workspace}:event:{number}",
                                    }
                                    for number in range(1, 4)
                                ],
                                "source_bindings": [f"fixture:{workspace}:event:{number}" for number in range(1, 4)],
                            },
                            "query": {
                                "status": "reviewed",
                                "required_slots": list(required_slots),
                                "expected_result_ids": expected_ids,
                                "query_form": query_id,
                            },
                            "answer": {"status": "reviewed", "expected_answer_ref": f"fixture:{workspace}:{query_id}"},
                            "governance": {
                                "status": "reviewed",
                                "variants": [{"id": variant_id, "admit": admit, "failure_class": failure_class}],
                            },
                        },
                    }
                )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = build_cases()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps({"case_count": len(rows), "fixture": "governed-memory-calibration.v1"}, sort_keys=True))


if __name__ == "__main__":
    main()
