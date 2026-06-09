---
name: mara-gateway
description: Use the MARA cloud gateway (OpenAI-compatible, multi-model) as the default generator + LLM-judge for SEOCHO benchmarks. Invoke when selecting an LLM for extraction/answer/judge, when a model id 404s, when hitting 429 rate limits, or when wiring a new provider/model into a benchmark run.
---

# mara-gateway

Purpose: one place that captures how to call MARA correctly so runs don't 404 on a mistyped model id, stall on rate limits, or skew results with a self-judging model. MARA is the repo's preferred live-experiment provider for generator + judge (CLAUDE.md §19 live-experiment default; `feedback_provider_cost_policy`).

## When to invoke

- Choosing the extraction / answer / judge LLM for any benchmark (`finder-graphrag-bench`, `finder_4arm_sample.py`, `graphrag_bench.py`).
- A call fails with `model_not_found` (404) or `RateLimitError` (429).
- Adding MARA to a new script via `seocho.store.llm.create_llm_backend` or `examples/finder/lib/llm_io.parse_llm_spec`.

## The gateway

- OpenAI-compatible. `base_url = https://api.cloud.mara.com/v1`, key env `MARA_API_KEY`.
- Provider id is **`mara`** — registered in BOTH `src/seocho/store/llm.py` (`_PROVIDER_SPECS`) and `examples/finder/lib/llm_io.py` (`_PROVIDER_PRESETS`). Use it as `mara/<Model>`.
- Drives any benchmark via `--llm mara/<Model>` (extraction) or `--llms mara/<Model>,...` / `--judge mara/<Model>` (compare).

## The 4 models — EXACT case-sensitive ids (gateway 404s on wrong casing)

| id (use verbatim) | notes |
|---|---|
| `DeepSeek-V3.1` | strong default generator; **~1500 RPD** rate limit (tightest) |
| `MiniMax-M2.5` | capital `M`s; may echo a reasoning trace in `content` |
| `MiniMax-M2.7` | capital `M`s |
| `gpt-oss-120b` | may return `content=None`; rely on JSON-parse fallback |

`minimax-m2.5` (lowercase) → 404. Always pass the exact casing. `client.models.list()` returns the authoritative ids.

## Rate-limit discipline (429)

- DeepSeek-V3.1 ≈ 1500 requests/day. A multi-LLM × multi-mode sweep blows through this fast, **especially if DeepSeek is both an answer model AND the judge** (doubles its load).
- **Separate the judge from the heaviest answer model.** Prefer `--judge mara/gpt-oss-120b` (or another model) when DeepSeek-V3.1 is in the answer set.
- Use the patient 429 retry schedule in `examples/finder/lib/llm_io.py:with_retry` — generic transient errors retry 3× (1/4/16s) but **429 retries 6× with 10/30/60/90/120s backoff**. Don't shorten it; the gateway throttles for tens of seconds.
- Mind the **daily** quota: if a long sweep starts 429-ing across all slices, you are likely at the daily cap — pause and resume after reset rather than burning retries.
- Cost-sensitivity (`feedback_cost_sensitivity_llm_calls`): the user pays every call. Never re-run a sweep that already produced partials; resume from checkpoints.

## Timeouts

Large-context answers (reasoning_mode + big evidence) can exceed the default 120s. Set `FINDER_LLM_TIMEOUT=300` for extraction/answer runs that include long cases (e.g. footnotes/debt with 5KB+ evidence).

## Verify done

A 1-token ping to each requested model returns `finish_reason` without 404; the judge model is not the same as the dominant answer model (unless self-judging is explicitly intended and tagged).
