#!/usr/bin/env python3
"""Sync SEOCHO's public GitHub label taxonomy without deleting labels."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any


HEX_COLOR = re.compile(r"^[0-9a-fA-F]{6}$")


def load_labels(path: Path) -> list[dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a JSON array")

    seen: set[str] = set()
    labels: list[dict[str, str]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"label #{index + 1} is not an object")
        name = str(item.get("name", "")).strip()
        color = str(item.get("color", "")).strip().lstrip("#").lower()
        description = str(item.get("description", "")).strip()
        if not name:
            raise ValueError(f"label #{index + 1} has no name")
        if name in seen:
            raise ValueError(f"duplicate label name: {name}")
        if not HEX_COLOR.match(color):
            raise ValueError(f"{name}: color must be a 6-character hex value")
        if len(description) > 100:
            raise ValueError(f"{name}: description is longer than GitHub allows")
        seen.add(name)
        labels.append({"name": name, "color": color, "description": description})
    return labels


def run_gh(args: list[str]) -> str:
    completed = subprocess.run(
        ["gh", *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def existing_labels(repo: str) -> dict[str, dict[str, Any]]:
    output = run_gh(
        [
            "label",
            "list",
            "--repo",
            repo,
            "--limit",
            "500",
            "--json",
            "name,color,description",
        ]
    )
    return {str(item["name"]): item for item in json.loads(output)}


def sync_labels(repo: str, labels: list[dict[str, str]], dry_run: bool = False) -> None:
    existing = existing_labels(repo)
    for label in labels:
        name = label["name"]
        current = existing.get(name)
        if current is None:
            action = "create"
            command = [
                "label",
                "create",
                name,
                "--repo",
                repo,
                "--color",
                label["color"],
                "--description",
                label["description"],
            ]
        else:
            color_changed = str(current.get("color", "")).lower() != label["color"]
            description_changed = str(current.get("description", "")) != label["description"]
            if not color_changed and not description_changed:
                print(f"ok {name}")
                continue
            action = "edit"
            command = [
                "label",
                "edit",
                name,
                "--repo",
                repo,
                "--color",
                label["color"],
                "--description",
                label["description"],
            ]
        print(f"{action} {name}")
        if not dry_run:
            run_gh(command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", default=".github/labels.json")
    parser.add_argument("--repo", help="owner/repo to sync with gh")
    parser.add_argument("--check", action="store_true", help="validate only")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    labels = load_labels(Path(args.labels))
    if args.check:
        print(f"validated {len(labels)} labels")
        return 0
    if not args.repo:
        parser.error("--repo is required unless --check is used")
    sync_labels(args.repo, labels, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
