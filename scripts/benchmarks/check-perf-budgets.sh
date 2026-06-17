#!/usr/bin/env bash
# Perf/behavioral budget gate (seocho-6q9.2, area-benchmark).
#
# Asserts SEOCHO trace latency budgets against a JSONL trace file. Exits
# non-zero (naming offending spans) when startup exceeds its budget or any
# span exceeds its ceiling. Run after a benchmark that emits JSONL traces:
#
#   SEOCHO_TRACE_BACKEND=jsonl python scripts/benchmarks/run_finder_benchmark.py ...
#   bash scripts/benchmarks/check-perf-budgets.sh
#
# Env:
#   SEOCHO_TRACE_JSONL_PATH  trace file (default: traces/seocho.jsonl)
#   SEOCHO_PERF_BUDGETS      budget JSON (default: scripts/benchmarks/perf_budgets.json)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"

TRACE_PATH="${SEOCHO_TRACE_JSONL_PATH:-${ROOT}/traces/seocho.jsonl}"
BUDGETS="${SEOCHO_PERF_BUDGETS:-${HERE}/perf_budgets.json}"

# Resolve a Python: explicit $PYTHON, then the repo venv, then PATH.
if [[ -n "${PYTHON:-}" ]]; then
  :
elif [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  PYTHON="python"
fi

if [[ ! -f "${TRACE_PATH}" ]]; then
  echo "perf-budget gate: no trace file at ${TRACE_PATH}" >&2
  echo "  run a benchmark with SEOCHO_TRACE_BACKEND=jsonl first." >&2
  exit 2
fi

echo "perf-budget gate: ${TRACE_PATH} against ${BUDGETS}"
exec "${PYTHON}" "${HERE}/perf_budget.py" --path "${TRACE_PATH}" --config "${BUDGETS}"
