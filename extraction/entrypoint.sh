#!/bin/bash
set -euo pipefail

# The API server is this image's job. Jupyter and the batch pipeline used to run
# alongside it unconditionally, and both were problems:
#
#   Jupyter ran as `--allow-root --NotebookApp.token=''` on 0.0.0.0:8888. Anyone
#   reaching that port got arbitrary code execution as root, and once compose
#   started injecting `.env` into the container that shell held MARA_API_KEY,
#   NEO4J_PASSWORD, SEOCHO_AUTH_SECRET and every other secret. The only control
#   was SEOCHO_BIND_HOST defaulting to 127.0.0.1 -- and our own docs tell
#   operators to set it to 0.0.0.0.
#
#   `python -m extraction.main` ran a full extraction pipeline with graph writes
#   on every container start. At one replica that is a surprise; at N replicas it
#   is N concurrent ingests racing the same graph and applying DDL concurrently.
#
# Both are now opt-in and default to off. Turning Jupyter on requires a token.

JUPYTER_ENABLED="${SEOCHO_ENABLE_JUPYTER:-0}"
BATCH_ENABLED="${SEOCHO_RUN_BATCH_ON_START:-0}"

if [ "${JUPYTER_ENABLED}" = "1" ]; then
  if [ -z "${SEOCHO_JUPYTER_TOKEN:-}" ]; then
    echo "SEOCHO_ENABLE_JUPYTER=1 requires SEOCHO_JUPYTER_TOKEN to be set." >&2
    echo "A tokenless notebook server is remote code execution for anyone who" >&2
    echo "can reach the port, with every secret in .env in its environment." >&2
    exit 1
  fi
  echo "Starting Jupyter Lab (token required)..."
  jupyter lab --ip=0.0.0.0 --port=8888 --no-browser \
    --ServerApp.token="${SEOCHO_JUPYTER_TOKEN}" &
fi

if [ "${BATCH_ENABLED}" = "1" ]; then
  BATCH_STATUS_FILE="${SEOCHO_BATCH_STATUS_FILE:-/tmp/seocho_batch_status}"
  echo "running" > "${BATCH_STATUS_FILE}"
  echo "Running Pipeline (extraction.main)..."
  if python -m extraction.main; then
    echo "success" > "${BATCH_STATUS_FILE}"
  else
    echo "failed" > "${BATCH_STATUS_FILE}"
    echo "extraction.main failed" >&2
  fi
fi

# exec, so uvicorn becomes PID 1. Previously it was backgrounded behind
# `tail -f /dev/null`, and `set -e` does not apply to background jobs -- so a
# dead API server left the container "up" forever, with no healthcheck and no
# restart policy to notice. As PID 1 its exit is the container's exit, which is
# what makes `restart:` and k8s liveness probes work at all.
echo "Starting Agent Server..."
exec uvicorn extraction.agent_server:app --host 0.0.0.0 --port 8001
