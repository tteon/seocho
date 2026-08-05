#!/usr/bin/env bash
# Detached launcher for the MA adjudication run (same pattern as run_reextract.sh).
set -euo pipefail
cd "$(dirname "$0")/../.."
LOG="outputs/evaluation/sweep_logs/ma_$(date +%m%d_%H%M).log"
setsid nohup .venv/bin/python -u experiments/disagreement_adjudication.py "$@" \
  > "$LOG" 2>&1 < /dev/null &
echo "$!" > outputs/evaluation/sweep_logs/ma.pid
echo "pid $(cat outputs/evaluation/sweep_logs/ma.pid)  log $LOG"
