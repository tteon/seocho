from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "pm" / "install-git-hooks.sh"
PRE_COMMIT_HOOK = REPO_ROOT / ".githooks" / "pre-commit"


def test_install_git_hooks_dry_run() -> None:
    result = subprocess.run(
        [str(INSTALL_SCRIPT), "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "core.hooksPath .githooks" in result.stdout


def test_pre_commit_hook_flushes_with_explicit_db(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, text=True, check=True)

    beads_dir = repo / ".beads"
    beads_dir.mkdir()
    (beads_dir / "beads.db").write_text("db", encoding="utf-8")
    (beads_dir / "issues.jsonl").write_text("{}\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls_file = tmp_path / "bd_calls.txt"
    fake_bd = fake_bin / "bd"
    fake_bd.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "{calls_file}"
exit 0
""",
        encoding="utf-8",
    )
    fake_bd.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [str(PRE_COMMIT_HOOK)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    recorded = calls_file.read_text(encoding="utf-8")
    assert "--no-daemon sync --flush-only --db" in recorded
    assert str(beads_dir / "beads.db") in recorded
