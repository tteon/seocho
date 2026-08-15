#!/usr/bin/env bash
# Boot a vLLM server with the KV-event stream enabled, so the block-level cache
# behaviour of a graph-agentic-RAG workload is observable from outside.
#
# The same script serves both rigs. Nothing below is specific to a GPU count or
# a model — the target environment is expressed entirely through env vars, so
# the RTX 3070 pipe-cleaner and the H200 x4 measurement run use identical code:
#
#   pipe-cleaner (1x RTX 3070, 8 GiB)
#     scripts/serve_track/launch_vllm.sh
#
#   measurement (4x H200, MiniMax-M2.7)
#     SERVE_MODEL=MiniMaxAI/MiniMax-M2.7 SERVE_TP=4 SERVE_GPU_MEM=0.90 \
#       scripts/serve_track/launch_vllm.sh
#
# The KV-event endpoint topology is not obvious and was measured on 0.27.1:
# vLLM's ZmqEventPublisher BINDS only when the endpoint contains a wildcard.
# With a concrete host:port it CONNECTs, which makes the *subscriber* the
# stable bound end — hence `--bind` on the probe side. Keep the concrete form
# here so the probe can outlive a server restart.
set -euo pipefail

SERVE_MODEL="${SERVE_MODEL:-Qwen/Qwen3-0.6B}"
SERVE_TP="${SERVE_TP:-1}"
SERVE_PORT="${SERVE_PORT:-8000}"
SERVE_GPU_MEM="${SERVE_GPU_MEM:-0.85}"
SERVE_MAX_LEN="${SERVE_MAX_LEN:-8192}"
SERVE_KV_ENDPOINT="${SERVE_KV_ENDPOINT:-tcp://127.0.0.1:5557}"
SERVE_VENV="${SERVE_VENV:-$HOME/.venvs/vllm-serve}"

if [ ! -x "$SERVE_VENV/bin/vllm" ]; then
  echo "vllm not found at $SERVE_VENV/bin/vllm" >&2
  echo "create it with: uv venv --python 3.10 $SERVE_VENV && \\" >&2
  echo "  VIRTUAL_ENV=$SERVE_VENV uv pip install 'vllm==0.27.1' pyzmq msgpack" >&2
  exit 1
fi

# Prefix caching is the mechanism under study; enabling it explicitly keeps the
# run honest if a future vLLM default flips.
exec "$SERVE_VENV/bin/vllm" serve "$SERVE_MODEL" \
  --port "$SERVE_PORT" \
  --tensor-parallel-size "$SERVE_TP" \
  --gpu-memory-utilization "$SERVE_GPU_MEM" \
  --max-model-len "$SERVE_MAX_LEN" \
  --enable-prefix-caching \
  --kv-events-config "{\"enable_kv_cache_events\": true, \"publisher\": \"zmq\", \"endpoint\": \"$SERVE_KV_ENDPOINT\"}"
