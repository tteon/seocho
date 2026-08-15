# Serve Track Runbook — dissecting graph-agentic RAG on vLLM

How to take a graph-agentic-RAG run apart stage by stage, attribute vLLM
KV-cache behaviour to each stage, and find the bottleneck worth engineering
against. Written for the PyTorch Serve Track talk.

The rig is deliberately split in two, because the target hardware is not here
yet and pretending otherwise would produce numbers that do not survive the move.

| Instrument | Answers | Runs on |
|---|---|---|
| **MARA API (MiniMax-M2.7)** | workload *shape* — per-stage tokens, latency, prompt composition | remote, available now |
| **Local vLLM** | *mechanism* — block identity, prefix reuse, eviction order | 1x RTX 3070 now, 4x H200 later |

Today the small local model is a pipe-cleaner: it proves the harness records
what it claims to. The measurement run happens when M2.7 is served on the H200
box — and nothing in `scripts/serve_track/` changes for that, only env vars.

## Why the join needs a sidecar

Neither side can attribute a block to a stage on its own:

- vLLM's KV events name no request. A `BlockStored` payload is
  `block_hashes + parent_block_hash + token_ids + medium`.
- The API response carries a per-request signal
  (`prompt_tokens_details.cached_tokens`) but says nothing about *which* blocks.
- Trace schema v1 records `latency_ms` per step but no absolute start time, and
  it was frozen in #486.

So `scripts/serve_track/kv_windows.py` records the one missing fact — when each
step ran, on the same `time.time` clock the KV subscriber stamps frames with —
into a sidecar next to the episode file. `correlate_kv.py` then joins by
containment. That is sound only when calls are serialized, which
`WindowRecorder` enforces by refusing to open a second window while one is open.

## Run it

### 1. Bring up vLLM with the KV-event stream

```bash
# pipe-cleaner: 1x RTX 3070, small model
scripts/serve_track/launch_vllm.sh

# measurement: 4x H200, MiniMax-M2.7 — same script
SERVE_MODEL=MiniMaxAI/MiniMax-M2.7 SERVE_TP=4 SERVE_GPU_MEM=0.90 \
  SERVE_MAX_LEN=32768 scripts/serve_track/launch_vllm.sh
```

The ZMQ topology is not obvious and was measured on 0.27.1: vLLM's
`ZmqEventPublisher` **binds only when the endpoint contains a wildcard**. With a
concrete `host:port` it connects, so the subscriber must be the bound end —
hence `--bind` below.

### 2. Subscribe to the events

```bash
python scripts/cache_probe/kv_events_probe.py \
  --base-url http://localhost:8000/v1 --model "$SERVE_MODEL" --bind \
  --out outputs/serve_track/<run>/kv_events.jsonl
```

### 3. Drive the RAG workload, recording windows

The harness writes three files into one run directory:

```
outputs/serve_track/<run>/episodes.jsonl    # trace schema v1, untouched
outputs/serve_track/<run>/kv_windows.jsonl  # per-step wall-clock extents
outputs/serve_track/<run>/kv_events.jsonl   # from step 2
```

### 4. Correlate

```bash
python scripts/serve_track/correlate_kv.py outputs/serve_track/<run> \
  --out outputs/serve_track/<run>/correlation.json
```

### 5. Prove the rig first

```bash
~/.venvs/vllm-serve/bin/python scripts/serve_track/smoke_correlation.py \
  --out-dir outputs/serve_track/smoke
```

Two shaped calls sharing a long prefix. Measured on 1x RTX 3070 / Qwen3-0.6B:

| stage | blocks stored | tokens stored | prompt tokens | reuse | latency |
|---|---:|---:|---:|---:|---:|
| `retrieve_ctx` (cold) | 55 | 880 | 885 | 0.6% | 789 ms |
| `synthesize` (warm) | 1 | 16 | 884 | **98.2%** | 542 ms |

Zero events unattributed. If the warm stage does not drop, the join is broken
or `--enable-prefix-caching` is off — fix that before trusting a real run.

The smoke salts its prefix per invocation. Without the salt the *previous* run's
blocks are still resident, the first call reuses them, and the result reads as
"no reuse" when the truth is "reuse, one run too early."

## The two cache signals, and which one to trust

| signal | source | works on |
|---|---|---|
| `prefix_reuse_rate` | blocks the engine did **not** store x `block_size` | vLLM |
| `cache_hit_rate` | `prompt_tokens_details.cached_tokens` | MARA |

**vLLM 0.27.1 does not populate `cached_tokens` at all** (measured — the field
is absent from the completions response). So on the vLLM rig the block side is
the only usable signal, and it is the better one: `BlockStored` carries
`block_size` (16), which converts blocks to tokens exactly. On the MARA rig
there are no block events and `cached_tokens` is the only signal — and it is
measured to report nothing on identical repeated prompts at or below ~204
tokens (#486), so short stages legitimately show `null`.

## KV offloading

vLLM can offload KV blocks to CPU or disk rather than dropping them, which
extends the reuse window past GPU HBM pressure — directly relevant when the
same ontology/schema prefix recurs across sessions on a contested GPU.

This rig already observes it: `BlockStored` carries a `medium` field, and
`correlate_kv.py` reports a per-stage `media` breakdown. On the pipe-cleaner run
everything is `{"GPU": 55}`. With offloading enabled, recalled blocks appear
under a non-GPU medium, so the question "did offloading actually save prefill,
or just move bytes?" is answerable from the same artifact — no new instrument.

## Per-request facts: the stat-logger plugin

The window/containment join exists only because vLLM's KV events name no
request. `FinishedRequestStats` does — and also carries `num_cached_tokens`,
`num_prompt_tokens`, and a queue/prefill/decode time breakdown. So the better
instrument is a plugin, and vLLM has a documented group for exactly this.

```bash
VIRTUAL_ENV=~/.venvs/vllm-serve uv pip install -e scripts/serve_track/vllm_plugin
SEOCHO_PROBE_OUT=outputs/serve_track/<run>/request_stats.jsonl \
  VLLM_PLUGINS=seocho_probe scripts/serve_track/launch_vllm.sh
```

Measured on 1x RTX 3070 / Qwen3-0.6B, two requests sharing an 847-token prefix:

| request | prompt tokens | cached | prefill |
|---|---:|---:|---:|
| cold | 847 | 0 | 47.5 ms |
| warm | 847 | 832 (98.2%) | 11.7 ms |

That prefill pair is the number the offloading decision turns on: recall is only
worth it when moving the bytes beats the ~47 ms of prefill it avoids.

Two reasons this is a plugin and not a patch of `vllm.v1.core`:
`EngineCoreProc` runs in its own process, so patching from the caller reaches
nothing; and `vllm.stat_logger_plugins` is a documented extension point, which
`vllm.v1.core.kv_cache_coordinator` is not. The caveat is in `StatLoggerBase`
itself: "the `SchedulerStats` and `IterationStats` classes are not considered
stable interfaces". Pin the vLLM version.

## Structured decoding does not cost prefix reuse

Constrained decoding shapes output tokens, not the prompt, and the measurement
agrees. Same prefix across four arms — plain, `response_format=json_object`,
and two distinct `guided_json` schemas — all reported **cached 832/847 (98.2%)**.

The cost is elsewhere and is one-time. The first guided request paid ~240 ms of
initialisation (325 ms vs ~85 ms plain); afterwards a brand-new schema cost
96 ms against 83 ms plain, and a repeated schema was indistinguishable from
plain (76 ms). Single request per arm — treat the millisecond deltas as
indicative, the 98.2% as solid.

## Where each stage's reuse ceiling comes from

| stage | system | user | what is stable |
|---|---:|---:|---|
| `plan` | ~3.2 KB ontology schema pack | ~55 char question | everything up to the question-scoped hints |
| `generation` | 639 char instruction block | question + retrieved records (74-1681 chars) | the instruction block only |

`generation` is already ordered correctly — stable system, volatile user — so
there is nothing to reorder there. Its ceiling is low because the retrieved
records genuinely differ per question. Raising it would mean moving stable
ontology context into its system prompt, which trades tokens for cacheability
and is a product decision, not a bug fix.

## Getting a graph the queries can actually match

Indexing with a pipe-cleaner model produces nothing, and under the default
`guided` enforcement that failure is silent: extraction falls back and writes
generic `Entity` nodes, so every `:Account` query matches nothing and synthesis
is handed an empty record set while the run still "succeeds". Use a capable
model for indexing and `--enforcement strict`, which refuses rather than falls
back:

```bash
python scripts/serve_track/profile_rag.py --out-dir <run> \
  --index-llm mara/MiniMax-M2.7 --index-api-key "$MARA_API_KEY"   # strict is the default
```

MiniMax-M2.7 is the model the H200 run will serve under vLLM, so indexing uses
it here too rather than an older MARA model.

`MARA_API_KEY` is quoted in `.env`; strip the quotes when exporting it, or the
key reaches the API with them attached and returns 401.

## Reading the output honestly

- **`prefix_reuse_rate: null` is per *run*, not per stage.** On a rig that emits
  block events, a stage that stored zero blocks reused everything — that is
  100%, the headline result, not a missing measurement. Only a run with no block
  evidence at all (an API-only rig) has nothing to report.
- **`events_unattributed` is reported, not spread.** `_recv_ts` is subscriber
  receive time, not engine emit time, so a delayed frame falls outside every
  window. Smoothing it into the stages would hide the error; a large count means
  the run is not trustworthy for attribution.
- **`stable_prefix_chars` compares section sizes, not contents.** A section that
  changes length has certainly changed tokens; one that keeps its length might
  still have changed. Treat it as an upper bound.

## What the H0 gate already ruled out

`ADR-0156` recorded the verdict: the Neo4j page cache and the vLLM KV cache hold
**diverging** working sets at scale (Jaccard below the 0.30 threshold on both
universes and hot sets). Co-managing the two caches under one budget — the
original WP3 — is dead, and the talk should not claim it.

What survives, and what this rig is for: the KV side on its own. Prefix reuse is
measured real (a shared 600-token head left 52 blocks unstored, 521ms → 60ms),
and vLLM v1 already evicts leaves-first (4,336 `BlockRemoved` events, zero
mid-chain removals), so tree-aware eviction is engine behaviour to validate, not
machinery to build. The engineering surface that remains is the one this runbook
measures: how the graph subgraph is serialized into the prompt, and whether that
serialization is stable enough to be a reusable prefix.

## Environment

- vLLM 0.27.1 in a dedicated venv (`~/.venvs/vllm-serve`), **Python 3.12**.
  3.10 fails at import: flashinfer's `fd_exchange.py` subscripts `array.array`,
  which needs 3.11+.
- The venv is separate from the repo's own, so the SDK's dependency set stays
  untouched by vLLM's pins.
