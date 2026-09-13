"""Exercise workspace operations with real disposable Git repositories."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/workspace/manage.py"
spec = importlib.util.spec_from_file_location("coding_workspace", SCRIPT)
assert spec and spec.loader
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo with spaces"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Workspace Test")
    _git(root, "config", "user.email", "test@example.com")
    (root / ".gitignore").write_text(".seocho/\n")
    (root / "code.py").write_text("original\n")
    _git(root, "add", ".gitignore", "code.py")
    _git(root, "commit", "-m", "fixture")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def test_start_preserves_dirty_source_and_creates_receipt(repo: Path) -> None:
    (repo / "code.py").write_text("user's unfinished work\n")
    before = _git(repo, "status", "--porcelain")
    receipt = workspace.start(repo, "issue-123")
    task = Path(receipt["checkout"])
    assert (repo / "code.py").read_text() == "user's unfinished work\n"
    assert _git(repo, "status", "--porcelain") == before
    assert _git(repo, "branch", "--show-current") == "main"
    assert (task / "code.py").read_text() == "original\n"
    assert _git(task, "status", "--porcelain") == ""
    assert json.loads((task / ".seocho/task.json").read_text())["task"] == "issue-123"
    assert (task / ".seocho/HANDOFF.md").exists()


def test_start_from_task_uses_common_workspace_root(repo: Path) -> None:
    first = Path(workspace.start(repo, "first")["checkout"])
    second = Path(workspace.start(first, "second")["checkout"])
    assert first.parent == second.parent == repo / ".seocho/worktrees"
    report = workspace.inspect(second)
    assert len(report["worktrees"]) == 3
    assert report["common_repository"] == str(repo)


@pytest.mark.parametrize("name", ["../outside", "a/b", "a..b", "-bad", "", "Bad Name"])
def test_invalid_task_cannot_create_checkout(repo: Path, name: str) -> None:
    with pytest.raises(ValueError):
        workspace.start(repo, name)
    assert len(workspace.inspect(repo)["worktrees"]) == 1


def test_existing_task_and_invalid_base_preserve_existing_work(repo: Path) -> None:
    receipt = workspace.start(repo, "existing")
    with pytest.raises(FileExistsError):
        workspace.start(repo, "existing")
    with pytest.raises(subprocess.CalledProcessError):
        workspace.start(repo, "missing-base", "no-such-ref")
    assert not (repo / ".seocho/worktrees/missing-base").exists()
    assert (Path(receipt["checkout"]) / "code.py").read_text() == "original\n"


def test_doctor_does_not_create_state(repo: Path) -> None:
    assert not workspace.inspect(repo)["dirty"]
    assert not (repo / ".seocho").exists()
