#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
dry_run=0

usage() {
  cat <<'EOF'
Usage:
  scripts/pm/install-git-hooks.sh [--dry-run]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      dry_run=1
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

if ! command -v git >/dev/null 2>&1; then
  echo "git is required" >&2
  exit 1
fi

HOOKS_DIR="${REPO_ROOT}/.githooks"
if [[ ! -d "${HOOKS_DIR}" ]]; then
  echo "Hooks directory not found: ${HOOKS_DIR}" >&2
  exit 1
fi

chmod +x "${HOOKS_DIR}/pre-commit"

if [[ "${dry_run}" -eq 1 ]]; then
  echo "[dry-run] would run: git -C \"${REPO_ROOT}\" config core.hooksPath .githooks"
  exit 0
fi

git -C "${REPO_ROOT}" config core.hooksPath .githooks

echo "Installed repo-managed hooks:"
echo "  core.hooksPath=.githooks"
echo "  pre-commit -> .githooks/pre-commit"
