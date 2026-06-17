from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples" / "contextgraph"))

from build_guardrail_case_pool import _scan_dataset
from mine_guardrail_candidates import main as mine_main
from promote_guardrail_artifact import main as promote_main
from promote_guardrail_artifact import _ontology_identity_hash
from scripts.benchmarks.run_finder_judge_chunks import _chunks


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "slice",
                "_id",
                "n_refs",
                "query_words",
                "query",
                "answer",
                "references_joined",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def test_scan_dataset_prefers_complete_threads(tmp_path: Path) -> None:
    csv_path = tmp_path / "slices.csv"
    _write_rows(
        csv_path,
        [
            {
                "slice": slice_name,
                "_id": f"complete#row{idx}",
                "n_refs": "2",
                "query_words": "4",
                "query": "q",
                "answer": "a",
                "references_joined": "evidence",
            }
            for idx, slice_name in enumerate(
                ["E1_FACT", "E2_DECISION_SUMMARY", "E3_PROPOSALS", "E4_POSITIONS"]
            )
        ]
        + [
            {
                "slice": "E1_FACT",
                "_id": "partial#row0",
                "n_refs": "1",
                "query_words": "3",
                "query": "q",
                "answer": "a",
                "references_joined": "short",
            }
        ],
    )

    result = _scan_dataset(str(csv_path), "unit", sample_size=1, seed=1)

    assert result["thread_count"] == 2
    assert result["complete_thread_count"] == 1
    assert result["selected"][0]["thread_id"] == "complete"
    assert result["selected"][0]["has_e4"] is True


def test_mine_guardrail_candidates_metrics_only(tmp_path: Path, monkeypatch) -> None:
    metrics_path = tmp_path / "metrics.jsonl"
    metrics_path.write_text(
        json.dumps(
            {
                "arm": "process_position",
                "illegal_relationships": 2,
                "illegal_relationship_types": ["OTHER", "MENTIONS"],
                "dropped_relationships": 2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "guardrail_candidate.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mine_guardrail_candidates.py",
            "--arm",
            "process_position",
            "--metrics-jsonl",
            str(metrics_path),
            "--workspace-prefix",
            "unit-run-process_position-",
            "--out",
            str(out),
        ],
    )

    assert mine_main() == 0
    payload = json.loads(out.read_text(encoding="utf-8"))

    summary = payload["source_summary"]
    assert summary["metrics"]["illegal_relationship_types"] == {"MENTIONS": 1, "OTHER": 1}
    term_labels = {
        item["pref_label"]
        for item in payload["vocabulary_candidate"]["terms"]
    }
    assert "blocked relation OTHER" in term_labels
    assert summary["graph_observation"]["available"] is False


def test_promote_guardrail_artifact_draft_round_trip(tmp_path: Path, monkeypatch) -> None:
    candidate = {
        "name": "unit-guardrail",
        "ontology_candidate": {
            "ontology_name": "unit",
            "classes": [],
            "relationships": [],
        },
        "shacl_candidate": {"shapes": []},
        "vocabulary_candidate": {"schema_version": "vocabulary.v2", "profile": "skos", "terms": []},
        "source_summary": {
            "graph_observation": {"ontology_context_hashes": ["", "hash-a"]}
        },
    }
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    out = tmp_path / "promotion.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "promote_guardrail_artifact.py",
            "--candidate",
            str(candidate_path),
            "--workspace-id",
            "unit_guardrails",
            "--artifact-dir",
            str(tmp_path / "artifacts"),
            "--out",
            str(out),
        ],
    )

    assert promote_main() == 0
    summary = json.loads(out.read_text(encoding="utf-8"))
    artifact = json.loads(Path(summary["artifact_path"]).read_text(encoding="utf-8"))

    assert summary["status"] == "draft"
    assert artifact["name"] == "unit-guardrail"
    assert artifact["ontology_identity_hash"] == "hash-a"
    assert _ontology_identity_hash({"source_summary": {"graph_observation": {"ontology_context_hashes": []}}}) == ""


def test_chunks_keeps_ordered_batches() -> None:
    assert _chunks(["a", "b", "c", "d", "e"], 2) == [["a", "b"], ["c", "d"], ["e"]]
