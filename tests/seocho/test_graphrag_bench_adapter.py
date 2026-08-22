from __future__ import annotations

import json

import pytest

from seocho.eval.graphrag_bench import (
    SCHEMA_VERSION,
    load_question_directory,
    write_jsonl,
)


def _row(question: str = "What is a SYN?") -> dict[str, object]:
    return {
        "Question": question,
        "Level-1 Topic": "Computer networks",
        "Level-2 Topic": "Network protocols",
        "Rationale": "A SYN starts a TCP connection.",
        "Answer": "TCP synchronization",
    }


def _question_dir(tmp_path) -> None:
    for question_type in ("FB", "MC", "MS", "OE", "TF"):
        (tmp_path / f"{question_type}.jsonl").write_text(
            json.dumps(_row(f"{question_type} question")) + "\n", encoding="utf-8"
        )


def test_loader_preserves_upstream_provenance_and_explicit_gaps(tmp_path) -> None:
    _question_dir(tmp_path)
    cases, manifest = load_question_directory(tmp_path)

    assert len(cases) == 5
    payload = cases[0].to_reference_dict()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "question" not in payload
    assert "answer" not in payload
    assert "rationale" not in payload
    assert payload["corpus_binding"]["status"] == "unbound"
    assert payload["text2cypher"]["status"] == "unannotated"
    assert payload["governance_variants"] == []
    assert manifest["text2cypher_annotation"]["unannotated"] == 5
    assert set(manifest["files"]) == {"FB", "MC", "MS", "OE", "TF"}


def test_loader_requires_all_official_question_files(tmp_path) -> None:
    (tmp_path / "FB.jsonl").write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="MC.jsonl"):
        load_question_directory(tmp_path)


def test_loader_marks_missing_rationale_as_unavailable_for_official_judging(
    tmp_path,
) -> None:
    _question_dir(tmp_path)
    invalid = _row()
    invalid.pop("Rationale")
    (tmp_path / "FB.jsonl").write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    cases, manifest = load_question_directory(tmp_path)
    assert cases[0].rationale is None
    assert cases[0].to_reference_dict()["rationale_status"] == "missing_upstream"
    assert manifest["files"]["FB"]["rationale_missing_total"] == 1


def test_loader_balances_smoke_subset_and_writes_jsonl(tmp_path) -> None:
    for question_type in ("FB", "MC", "MS", "OE", "TF"):
        rows = [_row(f"{question_type}-{index}") for index in range(2)]
        (tmp_path / f"{question_type}.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
        )
    cases, manifest = load_question_directory(tmp_path, limit_per_type=1)
    destination = tmp_path / "out" / "cases.jsonl"
    assert len(cases) == 5
    assert manifest["files"]["FB"]["rows_total"] == 2
    assert manifest["files"]["FB"]["rows_selected"] == 1
    assert write_jsonl(cases, destination) == 5
    rows = [
        json.loads(line)
        for line in destination.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 5
    assert all("question" not in row and "answer" not in row for row in rows)
