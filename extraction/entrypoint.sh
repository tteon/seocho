#!/bin/bash
set -e

# Start Jupyter Lab in background
echo "Starting Jupyter Lab..."
jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --NotebookApp.token='' &

# Start Agent Server (Uvicorn)
echo "Starting Agent Server..."
echo "Starting Agent Server..."
uvicorn agent_server:app --host 0.0.0.0 --port 8001 &

echo "Running Pipeline (main.py)..."
python main.py || echo "main.py failed or finished"

# Keep the container alive (if main.py finishes, we still want Jupyter running)
echo "Pipeline finished. Keeping container alive for Jupyter..."
tail -f /dev/null
