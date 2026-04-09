from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "beads-path-guard.sh"


def test_beads_path_guard_detects_redirect_conflict(tmp_path: Path) -> None:
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    (beads_dir / "config.yaml").write_text("redirect: /tmp/shared-beads\n", encoding="utf-8")
    (beads_dir / "beads.db").write_text("db", encoding="utf-8")
    (beads_dir / "issues.jsonl").write_text("{}\n", encoding="utf-8")

    result = subprocess.run(
        [str(SCRIPT_PATH), "--beads-dir", str(beads_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "status: fail" in result.stdout
    assert "Reason: redirect exists while local beads artifacts are present." in result.stdout
    assert str(beads_dir / "beads.db") in result.stdout


def test_beads_path_guard_auto_clean_resolves_conflict(tmp_path: Path) -> None:
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    (beads_dir / "config.yaml").write_text("redirect: /tmp/shared-beads\n", encoding="utf-8")
    (beads_dir / "beads.db").write_text("db", encoding="utf-8")
    (beads_dir / "issues.jsonl").write_text("{}\n", encoding="utf-8")

    result = subprocess.run(
        [
            str(SCRIPT_PATH),
            "--beads-dir",
            str(beads_dir),
            "--auto-clean",
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["conflict"] is False
    assert str(beads_dir / "beads.db") in payload["removed"]
    assert str(beads_dir / "issues.jsonl") in payload["removed"]
    assert not (beads_dir / "beads.db").exists()
    assert not (beads_dir / "issues.jsonl").exists()
