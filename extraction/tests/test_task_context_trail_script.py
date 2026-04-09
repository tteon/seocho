from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "task-context-trail.sh"


def _write_fake_bd(fake_bin_dir: Path) -> None:
    fake_bd = fake_bin_dir / "bd"
    fake_bd.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--no-daemon" ]]; then
  shift
fi

if [[ "${1:-}" == "show" && "${3:-}" == "--json" ]]; then
  task_id="${2}"
  cat <<JSON
[{
  "id": "${task_id}",
  "title": "Task ${task_id}",
  "status": "in_progress",
  "priority": 2,
  "issue_type": "task",
  "created_at": "2026-02-21T07:00:00Z",
  "updated_at": "2026-02-21T07:05:00Z"
}]
JSON
  exit 0
fi

echo "unsupported bd args: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_bd.chmod(0o755)


def _make_events(events_file: Path, task_id: str) -> None:
    rows = [
        {
            "schema_version": "cg.v0",
            "event_id": "evt_1",
            "task_id": task_id,
            "run_id": "run_1",
            "event_type": "run_started",
            "timestamp": "2026-02-21T07:00:01Z",
            "scope": "town",
            "payload": {"script": "ops-check"},
            "source_ref": "scripts/ops-check.sh",
        },
        {
            "schema_version": "cg.v0",
            "event_id": "evt_2",
            "task_id": task_id,
            "run_id": "run_1",
            "event_type": "gate_result",
            "timestamp": "2026-02-21T07:00:02Z",
            "scope": "town",
            "payload": {"gate": "workspace_check", "status": "pass"},
            "source_ref": "scripts/ops-check.sh",
        },
        {
            "schema_version": "cg.v0",
            "event_id": "evt_3",
            "task_id": task_id,
            "run_id": "run_2",
            "event_type": "landing_result",
            "timestamp": "2026-02-21T07:00:03Z",
            "scope": "town",
            "payload": {"status": "pass", "failures": 0, "warnings": 0},
            "source_ref": "scripts/gt-land.sh",
        },
        {
            "schema_version": "cg.v0",
            "event_id": "evt_x",
            "task_id": "hq-other",
            "run_id": "run_x",
            "event_type": "run_started",
            "timestamp": "2026-02-21T07:00:04Z",
            "scope": "town",
            "payload": {"script": "ignored"},
            "source_ref": "scripts/ops-check.sh",
        },
    ]
    events_file.write_text(
        "\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def _env_with_fake_bd(fake_bin_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin_dir}:{env['PATH']}"
    return env


def test_task_context_trail_text_output(tmp_path: Path) -> None:
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    _write_fake_bd(fake_bin_dir)

    events_file = tmp_path / "events.jsonl"
    _make_events(events_file, task_id="hq-trail")

    result = subprocess.run(
        [
            str(SCRIPT_PATH),
            "--task-id",
            "hq-trail",
            "--events-file",
            str(events_file),
            "--limit",
            "10",
        ],
        cwd=REPO_ROOT,
        env=_env_with_fake_bd(fake_bin_dir),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Task Context Trail: hq-trail" in result.stdout
    assert "Gate Checks (1):" in result.stdout
    assert "Landing Results (1):" in result.stdout


def test_task_context_trail_json_output(tmp_path: Path) -> None:
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    _write_fake_bd(fake_bin_dir)

    events_file = tmp_path / "events.jsonl"
    _make_events(events_file, task_id="hq-json")

    result = subprocess.run(
        [
            str(SCRIPT_PATH),
            "--task-id",
            "hq-json",
            "--events-file",
            str(events_file),
            "--json",
        ],
        cwd=REPO_ROOT,
        env=_env_with_fake_bd(fake_bin_dir),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["task"]["id"] == "hq-json"
    assert payload["event_count"] == 3
    assert len(payload["checks"]) == 1
    assert payload["checks"][0]["gate"] == "workspace_check"
    assert len(payload["landing_results"]) == 1
    assert payload["landing_results"][0]["status"] == "pass"
