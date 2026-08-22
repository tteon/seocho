from seocho.eval.case_envelope import (
    CASE_ENVELOPE_SCHEMA_VERSION,
    annotation_coverage,
    case_receipt,
    validate_case_envelope,
)


def _case() -> dict:
    return {
        "schema_version": CASE_ENVELOPE_SCHEMA_VERSION,
        "case_id": "fixture:0001",
        "source": {"snapshot_sha256": "a" * 64, "document_refs": ["doc:1"]},
        "layers": {
            "ontology": {"status": "reviewed", "required_terms": ["Intent"]},
            "triples": {
                "status": "reviewed",
                "gold_triples": [{"source": "i", "relation": "HAS_EVENT", "target": "e"}],
                "source_bindings": ["doc:1#span:1"],
            },
            "query": {
                "status": "reviewed",
                "required_slots": ["step"],
                "expected_result_ids": ["event:1"],
            },
            "answer": {"status": "unannotated"},
            "governance": {"status": "reviewed", "variants": [{"id": "stale", "admit": False}]},
        },
    }


def test_case_envelope_keeps_missing_answer_gold_explicit() -> None:
    case = _case()
    assert validate_case_envelope(case) == []
    receipt = case_receipt(case)
    assert receipt["valid"] is True
    assert receipt["scorable_layers"] == ["governance", "ontology", "query", "triples"]
    assert receipt["case_sha256"]


def test_reviewed_layer_requires_its_own_gold_fields() -> None:
    case = _case()
    case["layers"]["query"] = {"status": "reviewed"}
    assert "reviewed query layer requires required_slots" in validate_case_envelope(case)


def test_annotation_coverage_does_not_turn_unannotated_into_zero_score() -> None:
    report = annotation_coverage([_case()])
    assert report["invalid_case_count"] == 0
    assert report["layers"]["answer"] == {
        "reviewed": 0,
        "unannotated": 1,
        "unavailable": 0,
        "coverage_rate": 0.0,
    }
