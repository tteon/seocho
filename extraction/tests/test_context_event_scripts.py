from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_CHECK_SCRIPT = REPO_ROOT / "scripts" / "ops-check.sh"
GT_LAND_SCRIPT = REPO_ROOT / "scripts" / "gt-land.sh"
SCHEMA_PATH = REPO_ROOT / "docs" / "schemas" / "context-event.schema.json"
REQUIRED_FIELDS = {
    "schema_version",
    "event_id",
    "task_id",
    "run_id",
    "event_type",
    "timestamp",
    "scope",
    "payload",
    "source_ref",
}
ALLOWED_EVENT_TYPES = {
    "task_claimed",
    "run_started",
    "artifact_changed",
    "gate_result",
    "landing_result",
    "run_finished",
    "task_closed",
}


def _read_events(events_file: Path) -> list[dict[str, object]]:
    lines = [line.strip() for line in events_file.read_text().splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def _assert_event_contract(event: dict[str, object]) -> None:
    assert REQUIRED_FIELDS.issubset(event.keys())
    assert event["schema_version"] == "cg.v0"
    assert event["event_type"] in ALLOWED_EVENT_TYPES
    assert isinstance(event["payload"], dict)


def test_context_event_schema_has_expected_contract() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    assert schema["properties"]["schema_version"]["const"] == "cg.v0"
    assert set(schema["required"]) == REQUIRED_FIELDS
    assert set(schema["properties"]["event_type"]["enum"]) == ALLOWED_EVENT_TYPES


def test_ops_check_emits_context_events(tmp_path: Path) -> None:
    events_file = tmp_path / "ops-check-events.jsonl"
    result = subprocess.run(
        [
            str(OPS_CHECK_SCRIPT),
            "--task-id",
            "hq-test-ops",
            "--scope",
            "ci",
            "--events-file",
            str(events_file),
            "--skip-agent-doc-lint",
            "--quiet-events",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    events = _read_events(events_file)
    event_types = {event["event_type"] for event in events}
    assert {"run_started", "gate_result", "run_finished"}.issubset(event_types)
    for event in events:
        _assert_event_contract(event)


def test_gt_land_emits_landing_events_in_dry_run(tmp_path: Path) -> None:
    events_file = tmp_path / "gt-land-events.jsonl"
    result = subprocess.run(
        [
            str(GT_LAND_SCRIPT),
            "--task-id",
            "hq-test-land",
            "--scope",
            "ci",
            "--events-file",
            str(events_file),
            "--skip-bd-sync",
            "--dry-run",
            "--quiet-events",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    events = _read_events(events_file)
    event_types = {event["event_type"] for event in events}
    assert {"run_started", "landing_result", "run_finished"}.issubset(event_types)
    for event in events:
        _assert_event_contract(event)
