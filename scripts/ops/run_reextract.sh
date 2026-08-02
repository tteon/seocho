#!/usr/bin/env bash
# Launch the ontology-arm re-extraction so it outlives this session.
#
# setsid + nohup reparents to PID 1, so a terminal or agent-session restart
# cannot SIGTERM a multi-hour paid run (CLAUDE.md 13). The runner is resume-safe,
# so re-running this script continues rather than recomputing.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p outputs/evaluation/sweep_logs
LOG="outputs/evaluation/sweep_logs/rx_$(date +%m%d_%H%M).log"
setsid nohup .venv/bin/python -u experiments/minimal/reextract.py "$@" \
  > "$LOG" 2>&1 < /dev/null &
echo "$!" > outputs/evaluation/sweep_logs/rx.pid
echo "pid $(cat outputs/evaluation/sweep_logs/rx.pid)  log $LOG"
