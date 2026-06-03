#!/usr/bin/env bash
# Launch a FinDER sweep DETACHED from the calling shell/agent session so it
# survives session restarts, terminal close, or the controlling process being
# killed (root cause of the 2026-06-02 overnight loss: the sweep ran as a child
# of the interactive session and was SIGTERM'd when that session restarted).
#
# How it survives: `setsid` reparents the worker into its OWN session/process
# group (parent → PID 1), so a kill aimed at the launcher's session/group does
# not reach it. Pairs with the resume guard in finder_4arm_sample.py — if the
# box itself reboots, just re-run the SAME command and it resumes from the
# on-disk partials (skips matching prompt_hash+ontology_hash) instead of
# re-spending API quota from zero.
#
# `python3 -u` keeps stdout unbuffered so the log file shows live progress
# (buffered runs looked "stalled" with a 0-byte log even while working).
#
# Usage:
#   scripts/benchmarks/run_sweep_detached.sh mara/DeepSeek-V3.1 --n-per-slice 5 --run-prefix sweep-mara-ds
#   tail -f outputs/evaluation/sweep_logs/<slug>-*.log      # watch progress
#   kill "$(cat outputs/evaluation/sweep_logs/<slug>.pid)"  # stop it
set -euo pipefail

MODEL="${1:?usage: run_sweep_detached.sh <provider/model> [finder_4arm_sample.py args...]}"
shift || true

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SLUG="$(printf '%s' "$MODEL" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//')"
LOGDIR="$ROOT/outputs/evaluation/sweep_logs"
mkdir -p "$LOGDIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGDIR/${SLUG}-${STAMP}.log"
PIDFILE="$LOGDIR/${SLUG}.pid"

cd "$ROOT"
setsid nohup python3 -u scripts/benchmarks/finder_4arm_sample.py \
    --llm "$MODEL" "$@" > "$LOG" 2>&1 < /dev/null &
PID=$!
echo "$PID" > "$PIDFILE"
disown "$PID" 2>/dev/null || true

echo "launched (detached, survives session restart): model=$MODEL pid=$PID"
echo "  log: $LOG"
echo "  pid: $PIDFILE"
echo "  resume-safe: re-run the same command to continue from on-disk partials"
