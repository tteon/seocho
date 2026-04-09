#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  scripts/beads-path-guard.sh [options]

Options:
  --beads-dir <path>   Beads workspace directory (default: .beads).
  --auto-clean         Remove local runtime artifacts if conflict is detected.
  --json               Emit machine-readable JSON.
  -h, --help           Show this help text.
EOF
}

beads_dir="${REPO_ROOT}/.beads"
auto_clean=0
json_output=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --beads-dir)
      beads_dir="$2"
      shift 2
      ;;
    --auto-clean)
      auto_clean=1
      shift
      ;;
    --json)
      json_output=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

BEADS_DIR="${beads_dir}" \
AUTO_CLEAN="${auto_clean}" \
JSON_OUTPUT="${json_output}" \
python3 - <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


LOCAL_ARTIFACT_GLOBS = [
    "beads.db",
    "beads.db-*",
    "daemon.lock",
    "daemon.pid",
    "daemon.log",
    "interactions.jsonl",
    "issues.jsonl",
    "last-touched",
    ".jsonl.lock",
    "export-state/*.json",
]

REDIRECT_MARKERS = [
    "redirect",
    ".redirect",
    "redirect_path",
    "path.redirect",
]


def _read_redirect_from_config(config_path: Path) -> str:
    if not config_path.exists():
        return ""
    for raw in config_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("redirect:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def _discover_redirect_target(beads_dir: Path) -> tuple[str, str]:
    # 1) Explicit redirect marker files.
    for marker in REDIRECT_MARKERS:
        marker_path = beads_dir / marker
        if marker_path.exists():
            value = marker_path.read_text().strip()
            if value:
                return value, str(marker_path)

    # 2) YAML config key.
    config_path = beads_dir / "config.yaml"
    config_value = _read_redirect_from_config(config_path)
    if config_value:
        return config_value, str(config_path)

    # 3) Symlinked .beads directory.
    if beads_dir.is_symlink():
        try:
            return os.readlink(beads_dir), f"{beads_dir} (symlink)"
        except OSError:
            return "", ""

    return "", ""


def _collect_local_artifacts(beads_dir: Path) -> list[str]:
    artifacts: set[str] = set()
    for pattern in LOCAL_ARTIFACT_GLOBS:
        for path in beads_dir.glob(pattern):
            if path.exists():
                artifacts.add(str(path))
    return sorted(artifacts)


def _is_ignored_artifact(path: Path) -> bool:
    name = path.name
    return name in {"README.md", "config.yaml", ".gitignore", "metadata.json", ".local_version"}


beads_dir = Path(os.environ["BEADS_DIR"])
auto_clean = os.environ["AUTO_CLEAN"] == "1"
json_output = os.environ["JSON_OUTPUT"] == "1"

if not beads_dir.exists():
    raise SystemExit(f"Beads directory not found: {beads_dir}")

redirect_target, redirect_source = _discover_redirect_target(beads_dir)
local_artifacts = _collect_local_artifacts(beads_dir)
conflict = bool(redirect_target and local_artifacts)
removed: list[str] = []

if conflict and auto_clean:
    for artifact in local_artifacts:
        artifact_path = Path(artifact)
        if not artifact_path.exists():
            continue
        if _is_ignored_artifact(artifact_path):
            continue
        artifact_path.unlink(missing_ok=True)
        removed.append(str(artifact_path))
    local_artifacts = _collect_local_artifacts(beads_dir)
    conflict = bool(redirect_target and local_artifacts)

result: dict[str, Any] = {
    "beads_dir": str(beads_dir),
    "redirect_target": redirect_target,
    "redirect_source": redirect_source,
    "local_artifacts": local_artifacts,
    "conflict": conflict,
    "auto_clean": auto_clean,
    "removed": removed,
}

if json_output:
    print(json.dumps(result, ensure_ascii=True, indent=2))
else:
    print("Beads Path Guard")
    print(f"beads_dir: {beads_dir}")
    print(f"redirect_target: {redirect_target or '(none)'}")
    print(f"local_artifacts: {len(local_artifacts)}")
    if conflict:
        print("status: fail")
        print("Reason: redirect exists while local beads artifacts are present.")
        for artifact in local_artifacts:
            print(f"- {artifact}")
        print("Auto-clean command:")
        print(f"  scripts/beads-path-guard.sh --beads-dir {beads_dir} --auto-clean")
    else:
        print("status: pass")
    if removed:
        print(f"removed: {len(removed)}")

if conflict:
    sys.exit(1)
sys.exit(0)
PY
