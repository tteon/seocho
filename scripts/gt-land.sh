#!/usr/bin/env bash
set -euo pipefail

RIG=""
PUSH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rig) RIG="${2:-}"; shift 2 ;;
    --push) PUSH=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

scripts/check-beads-path.sh .
if [[ -n "$RIG" ]]; then
  scripts/ops-check.sh --rig "$RIG" --fix
else
  scripts/ops-check.sh --fix
fi

bd sync

if [[ "$PUSH" -eq 1 ]]; then
  git pull --rebase
  git push
fi

git status -sb
