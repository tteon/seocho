from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "gt-doctor.sh"


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_gt_doctor_prints_misclassified_wisp_ids() -> None:
    issues_file = Path.cwd() / "tmp_gt_doctor_issues_1.jsonl"
    try:
        _write_jsonl(
            issues_file,
            [
                {
                    "id": "hq-wisp-ok",
                    "title": "Wisp patrol",
                    "issue_type": "task",
                    "ephemeral": True,
                },
                {
                    "id": "hq-wisp-bad-type",
                    "title": "Wisp bug",
                    "issue_type": "agent",
                    "ephemeral": True,
                },
                {
                    "id": "hq-wisp-bad-ephemeral",
                    "title": "Wisp triage",
                    "issue_type": "task",
                    "ephemeral": False,
                },
                {
                    "id": "hq-task-bad-label",
                    "title": "Unexpected label",
                    "issue_type": "task",
                    "labels": ["gt:wisp"],
                },
            ],
        )

        result = subprocess.run(
            [
                str(SCRIPT_PATH),
                "--issues-file",
                str(issues_file),
                "--issues-source",
                "file",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1
        assert "CLEANUP->misclassified-wisps: fail (3)" in result.stdout
        assert "hq-wisp-bad-type" in result.stdout
        assert "issue_type=agent (expected task|epic)" in result.stdout
        assert "hq-wisp-bad-ephemeral" in result.stdout
        assert "ephemeral=false (expected true)" in result.stdout
        assert "hq-task-bad-label" in result.stdout
        assert "gt:wisp label without -wisp- issue ID pattern" in result.stdout
    finally:
        issues_file.unlink(missing_ok=True)


def test_gt_doctor_json_output() -> None:
    issues_file = Path.cwd() / "tmp_gt_doctor_issues_2.jsonl"
    try:
        _write_jsonl(
            issues_file,
            [
                {
                    "id": "hq-wisp-ok",
                    "title": "Wisp patrol",
                    "issue_type": "task",
                    "ephemeral": True,
                }
            ],
        )

        result = subprocess.run(
            [
                str(SCRIPT_PATH),
                "--issues-file",
                str(issues_file),
                "--issues-source",
                "file",
                "--json",
                "--no-strict",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        check = next(item for item in payload["checks"] if item["name"] == "misclassified-wisps")
        assert check["name"] == "misclassified-wisps"
        assert check["status"] == "pass"
        assert check["count"] == 0
    finally:
        issues_file.unlink(missing_ok=True)


def test_gt_doctor_prefers_bd_source_in_auto_mode(tmp_path: Path) -> None:
    issues_file = tmp_path / "issues.jsonl"
    _write_jsonl(
        issues_file,
        [
            {
                "id": "hq-wisp-file-bad",
                "title": "File only bad wisp",
                "issue_type": "agent",
            }
        ],
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_bd = bin_dir / "bd"
    _write_executable(
        fake_bd,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--no-daemon" ]]; then
  shift
fi
cmd="${1:-}"
if [[ "$cmd" == "list" ]]; then
  cat <<'JSON'
[{"id":"hq-wisp-db-ok","title":"DB Wisp","issue_type":"task","ephemeral":true}]
JSON
  exit 0
fi
echo "unexpected command: $cmd" >&2
exit 1
""",
    )

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    result = subprocess.run(
        [str(SCRIPT_PATH), "--issues-file", str(issues_file), "--json", "--no-strict"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["issues_source"] == "bd"
    check = next(item for item in payload["checks"] if item["name"] == "misclassified-wisps")
    assert check["status"] == "pass"
    assert check["count"] == 0


def test_gt_doctor_falls_back_to_file_when_bd_unavailable(tmp_path: Path) -> None:
    issues_file = tmp_path / "issues.jsonl"
    _write_jsonl(
        issues_file,
        [
            {
                "id": "hq-wisp-file-bad",
                "title": "File fallback bad wisp",
                "issue_type": "agent",
            }
        ],
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_bd = bin_dir / "bd"
    _write_executable(
        fake_bd,
        """#!/usr/bin/env bash
set -euo pipefail
echo "bd unavailable" >&2
exit 1
""",
    )

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    result = subprocess.run(
        [str(SCRIPT_PATH), "--issues-file", str(issues_file), "--json", "--no-strict"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["issues_source"] == "issues_file"
    assert payload["source_warnings"]
    check = next(item for item in payload["checks"] if item["name"] == "misclassified-wisps")
    assert check["status"] == "fail"
    assert check["count"] == 1


def test_gt_doctor_fix_applies_bd_updates(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_file = tmp_path / "bd_state.txt"
    fake_bd = bin_dir / "bd"
    _write_executable(
        fake_bd,
        f"""#!/usr/bin/env bash
set -euo pipefail
state="{state_file}"
if [[ "${{1:-}}" == "--no-daemon" ]]; then
  shift
fi
cmd="${{1:-}}"
shift || true
if [[ "$cmd" == "list" ]]; then
  if [[ -f "$state" ]]; then
    cat <<'JSON'
[{{"id":"hq-wisp-fix","title":"Fix me","issue_type":"task","ephemeral":true}}]
JSON
  else
    cat <<'JSON'
[{{"id":"hq-wisp-fix","title":"Fix me","issue_type":"agent","ephemeral":false}}]
JSON
  fi
  exit 0
fi
if [[ "$cmd" == "update" ]]; then
  printf '%s\\n' "$*" > "$state"
  exit 0
fi
echo "unexpected command: $cmd $*" >&2
exit 1
""",
    )

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    result = subprocess.run(
        [str(SCRIPT_PATH), "--issues-source", "bd", "--fix", "--json"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    check = next(item for item in payload["checks"] if item["name"] == "misclassified-wisps")
    assert check["status"] == "pass"
    assert check["count"] == 0
    assert payload["fixes"]["count_applied"] == 1
    update_args = state_file.read_text(encoding="utf-8")
    assert "hq-wisp-fix" in update_args
    assert "--type task" in update_args
    assert "--ephemeral" in update_args


def test_gt_doctor_reports_single_beads_path_conflict(tmp_path: Path) -> None:
    issues_file = tmp_path / "issues.jsonl"
    _write_jsonl(
        issues_file,
        [
            {
                "id": "hq-task-ok",
                "title": "Normal task",
                "issue_type": "task",
                "labels": ["ops"],
            }
        ],
    )

    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    (beads_dir / "config.yaml").write_text("redirect: /tmp/shared-beads\n", encoding="utf-8")
    (beads_dir / "beads.db").write_text("db", encoding="utf-8")

    result = subprocess.run(
        [
            str(SCRIPT_PATH),
            "--issues-file",
            str(issues_file),
            "--beads-dir",
            str(beads_dir),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "CLEANUP->single-beads-path: fail (1)" in result.stdout
    assert str(beads_dir / "beads.db") in result.stdout


def test_gt_doctor_reports_runtime_file_isolation_violation(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)

    beads_dir = repo_root / ".beads"
    beads_dir.mkdir()
    (beads_dir / "issues.jsonl").write_text(
        json.dumps(
            {
                "id": "hq-task-ok",
                "title": "Normal task",
                "issue_type": "task",
                "labels": ["ops"],
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (beads_dir / "beads.db").write_text("db", encoding="utf-8")
    subprocess.run(
        ["git", "add", ".beads/beads.db", ".beads/issues.jsonl"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [
            str(SCRIPT_PATH),
            "--issues-file",
            str(beads_dir / "issues.jsonl"),
            "--beads-dir",
            str(beads_dir),
            "--repo-root",
            str(repo_root),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "CLEANUP->runtime-file-isolation: fail (1)" in result.stdout
    assert ".beads/beads.db" in result.stdout


def test_gt_doctor_reports_embedded_git_clone_violation(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)

    beads_dir = repo_root / ".beads"
    beads_dir.mkdir()
    (beads_dir / "issues.jsonl").write_text(
        json.dumps(
            {
                "id": "hq-task-ok",
                "title": "Normal task",
                "issue_type": "task",
                "labels": ["ops"],
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    embedded_clone = repo_root / "rig-clone"
    subprocess.run(
        ["git", "init", str(embedded_clone)],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [
            str(SCRIPT_PATH),
            "--issues-file",
            str(beads_dir / "issues.jsonl"),
            "--beads-dir",
            str(beads_dir),
            "--repo-root",
            str(repo_root),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "CLEANUP->embedded-git-clones: fail (1)" in result.stdout
    assert "rig-clone" in result.stdout
