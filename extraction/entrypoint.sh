#!/bin/bash
set -e

# Start Jupyter Lab in background
echo "Starting Jupyter Lab..."
jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --NotebookApp.token='' &

# Start Agent Server (Uvicorn)
echo "Starting Agent Server..."
echo "Starting Agent Server..."
uvicorn agent_server:app --host 0.0.0.0 --port 8001 &

BATCH_STATUS_FILE="${SEOCHO_BATCH_STATUS_FILE:-/tmp/seocho_batch_status}"
echo "running" > "${BATCH_STATUS_FILE}"

echo "Running Pipeline (main.py)..."
if python main.py; then
  echo "success" > "${BATCH_STATUS_FILE}"
else
  echo "failed" > "${BATCH_STATUS_FILE}"
  echo "main.py failed or finished"
fi

# Keep the container alive (if main.py finishes, we still want Jupyter running)
echo "Pipeline finished. Keeping container alive for Jupyter..."
tail -f /dev/null
