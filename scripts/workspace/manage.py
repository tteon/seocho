#!/usr/bin/env python3
"""Inspect the coding workspace or create an isolated task checkout.

No implicit fetch, install, cleanup, service launch, or change to the current
checkout. Local manifests live under the common repository's .seocho directory.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def repository(cwd: Path) -> tuple[Path, Path]:
    checkout = Path(git(cwd, "rev-parse", "--show-toplevel"))
    common = Path(git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return checkout, common.parent


def inspect(cwd: Path) -> dict[str, Any]:
    checkout, common = repository(cwd)
    status = git(checkout, "status", "--porcelain=v1", "--untracked-files=normal")
    worktrees: list[dict[str, str]] = []
    for block in git(checkout, "worktree", "list", "--porcelain").split("\n\n"):
        record = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            record[key] = value
        if record:
            worktrees.append(record)
    return {
        "schema_version": "seocho.coding_workspace.v1",
        "checkout": str(checkout), "common_repository": str(common),
        "branch": git(checkout, "rev-parse", "--abbrev-ref", "HEAD"),
        "revision": git(checkout, "rev-parse", "HEAD"),
        "dirty": bool(status), "changed_entries": len(status.splitlines()) if status else 0,
        "task_checkout_root": str(common / ".seocho" / "worktrees"),
        "worktrees": worktrees,
        "entrypoints": {"rules": "AGENTS.md", "workflow": "docs/AGENT_WORKFLOW.md",
                        "experiment": "docs/EXPERIMENT_PLATFORM.md", "decisions": "docs/decisions/DECISION_LOG.md"},
    }


def start(cwd: Path, task: str, base: str = "origin/main") -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", task) or ".." in task:
        raise ValueError("Task must be a short lowercase issue/task identifier, without path separators or '..'")
    if base.startswith("-") or any(ch.isspace() for ch in base):
        raise ValueError("Base must be a local Git reference; fetch explicitly before starting")
    checkout, common = repository(cwd)
    revision = git(checkout, "rev-parse", "--verify", f"{base}^{{commit}}")
    branch = f"codex/{task}"
    git(checkout, "check-ref-format", "--branch", branch)
    destination = common / ".seocho" / "worktrees" / task
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Task checkout already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    git(checkout, "worktree", "add", "-b", branch, str(destination), revision)
    # If writing the receipt fails, preserve the checkout; doctor can recover it.
    state = destination / ".seocho"
    state.mkdir(exist_ok=True)
    receipt = {
        "schema_version": "seocho.coding_task.v1", "task": task, "branch": branch,
        "checkout": str(destination), "base": base, "base_revision": revision,
        "created_at": datetime.now(timezone.utc).isoformat(), "validation": [],
    }
    (state / "task.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    (state / "HANDOFF.md").write_text(
        f"# Task {task}\n\nBranch: {branch}\nBase: {base} ({revision})\n\n"
        "## Objective and acceptance\n\nRecord the linked public issue/PR and expected user behavior.\n\n"
        "## Decisions\n\nLink the relevant ADR and ExecPlan; do not duplicate repository rules.\n\n"
        "## Changes and evidence\n\nRecord commands, results and remaining gaps as work progresses.\n\n"
        "## Next action\n\nRead AGENTS.md and docs/AGENT_WORKFLOW.md before editing.\n",
        encoding="utf-8",
    )
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Any checkout in the repository")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Read-only checkout/task inventory")
    doctor.add_argument("--json", action="store_true", dest="json_output")
    create = commands.add_parser("start", help="Create an isolated task worktree from an existing local ref")
    create.add_argument("task")
    create.add_argument("--base", default="origin/main")
    args = parser.parse_args(argv)
    try:
        result = start(args.root, args.task, args.base) if args.command == "start" else inspect(args.root)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Workspace operation failed: {getattr(exc, 'stderr', None) or exc}\n")
    if args.command == "start" or getattr(args, "json_output", False):
        print(json.dumps(result, indent=2))
    else:
        print(f"Checkout: {result['checkout']}\nBranch: {result['branch']}\n"
              f"Local changes: {result['changed_entries']}\nTask checkouts: {result['task_checkout_root']}")
        for tree in result["worktrees"]:
            print(f"  {tree.get('branch', 'detached')}: {tree['worktree']}")
        print("Read: AGENTS.md -> docs/AGENT_WORKFLOW.md -> task contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
