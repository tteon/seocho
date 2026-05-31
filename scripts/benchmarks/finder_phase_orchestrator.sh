#!/usr/bin/env bash
# Launch all FinDER phases in parallel and stream their progress.
# Each phase runs as its own python process; logs go to /tmp/finder_<phase>.log.
set -u

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
PHASES=("P0" "P1A" "P1B" "P1C" "P1D")
LOGDIR="/tmp/finder_phase_runs"
mkdir -p "$LOGDIR"
rm -f "$LOGDIR"/*.log "$LOGDIR"/*.pid

WORKSPACE_PREFIX="finder-phase-$(date -u +%Y%m%d%H%M%S)"
echo "workspace_prefix=$WORKSPACE_PREFIX"

PIDS=()
for p in "${PHASES[@]}"; do
  LOG="$LOGDIR/${p}.log"
  : > "$LOG"
  python3 -u "$ROOT/scripts/benchmarks/finder_phase_experiment.py" \
      --phases "$p" \
      --workspace-prefix "$WORKSPACE_PREFIX" --variants treatment \
      > "$LOG" 2>&1 &
  echo $! > "$LOGDIR/${p}.pid"
  PIDS+=("$!")
  echo "started $p (pid=$!)  log=$LOG"
done

echo "waiting for ${#PIDS[@]} phases…"
EXIT=0
for pid in "${PIDS[@]}"; do
  if ! wait "$pid"; then
    EXIT=$?
    echo "phase pid=$pid exited with $EXIT"
  fi
done
echo "all phases finished"
exit $EXIT
