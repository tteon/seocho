# ADR-0098: vLLM On-Prem LLM Profile

Date: 2026-05-25
Status: Proposed

## Context

The SDK's LLM access goes through `OpenAICompatibleBackend`
(`seocho/store/llm.py:269`), with provider presets registered in the
`_PROVIDER_SPECS` dict at `seocho/store/llm.py:33`. Today's presets cover
hosted providers only: `openai`, `deepseek`, `kimi`, `grok`, `qwen`. The
factory `create_llm_backend` (`seocho/store/llm.py:787`) routes by
provider string via an if/elif chain.

Three needs motivate adding a vLLM profile:

1. **GOPTS benchmarking on-prem.** ADR-0097's evaluation harness runs
   K-candidate enumeration per question over a fixture suite; sending
   that volume to a hosted provider during iteration is wasteful and
   data-residency-sensitive. An on-prem vLLM endpoint is the natural
   target.
2. **Customer parity.** Enterprise users running SEOCHO behind their
   own perimeter expect to point the SDK at a private inference
   endpoint without monkeypatching.
3. **Structured-output ergonomics.** vLLM exposes guided-JSON and
   guided-regex decoding via `extra_body` (sampling params). The
   pipeline-mode path (`response_format={"type":"json_object"}` at
   numerous call sites including `seocho/agent_config.py:591`,
   `seocho/query/planner.py:29`, `seocho/tools.py:63`) currently
   falls back to "Return ONLY valid JSON." prompt injection on failure
   (`seocho/store/llm.py:433–459`); vLLM's guided decoding would make
   that fallback dead code in pipeline mode.

The backend abstraction is already vLLM-ready: vLLM's HTTP API is
OpenAI-compatible. The Agents SDK adapter at `seocho/agents_runtime.py:67`
routes `Runner.run` through `to_agents_sdk_model()` on the backend
(`seocho/store/llm.py:558–604`), which builds an
`OpenAIChatCompletionsModel` against the configured base_url. No
mode gate is needed — Agents SDK tool-use rides the same OpenAI
chat-completions surface and vLLM serves it natively.

What's missing is the configuration shape, the factory routing, and the
explicit guided-decoding surface plus tests that prove the path.

## Decision

Add `vllm` as a first-class provider preset. No new abstraction; mirror
the existing five providers.

### 1. Provider preset

Add a `ProviderSpec` entry to `_PROVIDER_SPECS`
(`seocho/store/llm.py:33`):

```python
"vllm": ProviderSpec(
    api_key_env=("SEOCHO_VLLM_API_KEY", "VLLM_API_KEY"),
    base_url="http://localhost:8000/v1",        # vLLM default
    default_model=None,                          # required from caller
),
```

`base_url` resolution order matches existing providers:
caller-passed > env (`SEOCHO_VLLM_BASE_URL`, `VLLM_BASE_URL`) > spec
default. `api_key` is **optional** — vLLM runs unauthenticated by
default; the backend must accept `None` and pass `"EMPTY"` to the
underlying OpenAI client when absent (`OpenAI(api_key="EMPTY")` is the
documented vLLM convention).

### 2. Factory routing

Extend `create_llm_backend` (`seocho/store/llm.py:787`) with the
`vllm` case. Add a `VLLMBackend(OpenAICompatibleBackend)` subclass only
if vLLM-specific sampling-parameter overrides emerge during
implementation (mirror the Kimi temperature-clamp pattern at
`seocho/store/llm.py:320–325`). Default expectation: no subclass needed
for v1; revisit if vLLM's quirks (e.g. `top_k`, `min_p`) need
clamping.

### 3. Guided-decoding passthrough

Expose vLLM's guided-decoding via the existing `extra_body` merge path
(`_merge_extra_body` at `seocho/store/llm.py:328`). When the caller
passes a structured-output spec, the backend translates as follows:

| Caller intent                          | vLLM `extra_body`                  |
|----------------------------------------|------------------------------------|
| `response_format={"type":"json_object"}` | `{"guided_json": {"type":"object"}}` (permissive) |
| `response_format={"type":"json_schema","json_schema":S}` | `{"guided_json": S}` |
| `response_format={"type":"regex","pattern":P}` | `{"guided_regex": P}` |
| `response_format={"type":"choice","options":[...]}` | `{"guided_choice": [...]}` |

Guided decoding is **active in pipeline mode only**. In agent mode
(`Runner.run` via `to_agents_sdk_model`), the Agents SDK's tool-use
structure supersedes guided decoding and the backend must not force
JSON-shape on the model's tool-call output. This gating happens at the
`complete()` call site via a `mode: Literal["pipeline","agent"] | None`
flag threaded through `complete()` and defaulted from the caller
context — pipeline callers default to `"pipeline"`, agent callers
default to `"agent"`. When unset, behavior matches today's
prompt-injection fallback (no regression for callers that don't opt in).

### 4. Agents SDK tool-use coexistence test

New test in `tests/seocho/test_llm_backends.py` confirms `Runner.run`
against a mocked vLLM endpoint preserves tool calls end-to-end. Builds
on the existing Agents-SDK-binding tests at
`tests/seocho/test_llm_backends.py:130`. Specifically:

- mock the vLLM HTTP endpoint to return a `tool_calls` response
- invoke `Runner.run` via `seocho/agents_runtime.py:67`
- assert tool was called with the expected arguments

This is the contract test that proves the mode gate works.

### 5. End-to-end smoke

New test `test_vllm_backend_openai_compatible_chat_completions` in
`tests/seocho/test_llm_backends.py` covers:

- `Seocho.local(llm="vllm/<model>")` resolves via the factory
- `complete()` against a mocked vLLM HTTP endpoint
- `response_format={"type":"json_object"}` translates to `guided_json`
  in `extra_body`
- `to_agents_sdk_model()` binding succeeds

This is the acceptance gate for the epic.

## Consequences

Positive:

- enterprise users can configure on-prem vLLM with one provider string
  (`Seocho.local(llm="vllm/Qwen2.5-7B-Instruct")`) and no monkeypatch
- ADR-0097's GOPTS evaluation harness can run iterations against an
  on-prem endpoint, eliminating both cost and data-residency concerns
- guided decoding in pipeline mode removes the "Return ONLY valid JSON."
  prompt-injection fallback, replacing a probabilistic fix with a
  deterministic one
- no abstraction churn: factory if/elif stays, provider preset shape
  stays, Agents SDK adapter stays
- workspace_id propagation is unaffected; vLLM is just another
  OpenAI-compatible endpoint downstream of the existing tool closures

Tradeoffs:

- the mode gate (`"pipeline"` vs `"agent"`) is a new flag on
  `complete()`. Defaults preserve current behavior, but every new call
  site must remember to set it correctly; mitigated by call-site
  defaults from the caller context (pipeline modules default to
  `"pipeline"`, agent modules default to `"agent"`)
- vLLM-server deployment, model selection, and KV-cache sizing are out
  of scope for this ADR; they belong in `docs/RUNTIME_DEPLOYMENT.md`
  and are operator concerns
- multi-node vLLM (KV-cache sharing, tensor parallelism) is not
  addressed; the backend treats the vLLM endpoint as a single
  black-box HTTP API
- a future hosted vLLM-compatible service (e.g. Together, Anyscale)
  may want to reuse the `vllm` preset with a different `base_url`;
  this is supported via env var override and does not require a new
  provider entry

Open questions (deferred):

- whether `mode` should be inferred from caller context heuristics
  rather than threaded explicitly (start: explicit; revisit if
  call-site overhead is excessive)
- whether guided-decoding failures should fall back to the existing
  prompt-injection retry, or surface as hard errors (start: fall back;
  measure error rate)
- vLLM speculative-decoding flags (`use_speculative`,
  `draft_model`) — out of scope; revisit when GOPTS eval needs them

## Implementation Notes

- touch points:
  - `seocho/store/llm.py:33` — add `vllm` to `_PROVIDER_SPECS`
  - `seocho/store/llm.py:787` — extend `create_llm_backend` factory
    routing
  - `seocho/store/llm.py:328` — extend `_merge_extra_body` /
    `complete()` signature to handle the
    `response_format → guided_*` translation under `mode="pipeline"`
  - `tests/seocho/test_llm_backends.py` — Agents-SDK coexistence
    test and end-to-end smoke
  - `docs/RUNTIME_DEPLOYMENT.md` — operator note that the SDK now
    supports a vLLM provider preset; server-side deployment recipes
    remain out of scope for this ADR
- safety skills to invoke: `workspace-id-audit` (`complete()` callers
  must continue to propagate workspace_id; the mode gate must not be
  used as a side-channel to skip workspace scoping),
  `refactor-safety` (`complete()` signature change is multi-call-site;
  default values must preserve current behavior on untouched callers)
- aligns with CLAUDE.md §1 (vendor-neutral trace contract preserved),
  §6.1 (workspace_id continues to flow), §9 (observability metadata
  unchanged — trace preserves provider name), §15 (no direct
  `Runner.run`; Agents SDK still routes through
  `extraction/agents_runtime.py`), §18 (deterministic guided decoding
  replaces probabilistic retry in pipeline mode)
- depends on: nothing new in this repo; vLLM server is operator-side
- composes with: ADR-0097 (GOPTS evaluation harness consumes this
  preset)
- reference: vLLM OpenAI-compatible server docs
  (`vllm.entrypoints.openai.api_server`) and guided-decoding section
  of the vLLM serving guide.
