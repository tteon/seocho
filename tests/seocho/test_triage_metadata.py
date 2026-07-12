from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "triage_metadata.py"
SPEC = importlib.util.spec_from_file_location("triage_metadata", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
triage_metadata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(triage_metadata)


def _issue(title: str, body: str) -> dict[str, object]:
    return {"issue": {"title": title, "body": body}}


def _pr(title: str) -> dict[str, object]:
    return {"pull_request": {"title": title}}


def test_bug_issue_form_maps_kind_area_and_status() -> None:
    body = """### Area

query

### Reproduction

uv run pytest tests/seocho/test_cypher_builder.py -q
"""

    labels = triage_metadata.infer_labels(_issue("fix: Cypher escapes", body), [])

    assert "kind-bug" in labels
    assert "area-query" in labels
    assert "status-needs-triage" in labels
    assert "status-needs-repro" not in labels


def test_feature_issue_good_first_issue_and_design_labels() -> None:
    body = """### Area

connector

### Contribution size

good first issue
"""

    labels = triage_metadata.infer_labels(_issue("feat: Notion connector seed", body), [])

    assert "kind-feature" in labels
    assert "area-connector" in labels
    assert "good first issue" in labels


def test_pr_files_map_to_areas_and_title_kind() -> None:
    labels = triage_metadata.infer_labels(
        _pr("perf: stream JSONL files"),
        [
            "src/seocho/index/file_reader.py",
            "docs/OPEN_SOURCE_PLAYBOOK.md",
            ".github/workflows/ci-basic.yml",
        ],
    )

    assert "kind-perf" in labels
    assert "area-indexing" in labels
    assert "area-docs" in labels
    assert "area-ci" in labels


def test_docs_pr_defaults_to_docs_kind_from_file() -> None:
    labels = triage_metadata.infer_labels(
        _pr("Clarify quickstart wording"),
        ["QUICKSTART.md"],
    )

    assert "kind-docs" in labels
    assert "area-docs" in labels


def test_release_issue_title_maps_to_release_kind() -> None:
    labels = triage_metadata.infer_labels(_issue("release: v0.6.0", ""), [])

    assert "kind-release" in labels
    assert "status-needs-triage" in labels
    assert "kind-maintenance" not in labels
