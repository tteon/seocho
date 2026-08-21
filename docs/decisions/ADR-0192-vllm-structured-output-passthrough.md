# ADR-0192: Pass `response_format` through to vLLM; drop the guided-decoding translation

- Status: Accepted
- Date: 2026-08-16
- Supersedes: the guided-decoding translation in `ADR-0098` §3 (the rest of
  ADR-0098 — the vLLM provider preset, agent-mode tool-call handling — stands)
- Related: `ADR-0144` (observability), `seocho-fix` (unified cache)

## Context

ADR-0098 §3 specified that in pipeline mode against a vLLM endpoint, an OpenAI
`response_format` is translated into a guided-decoding `extra_body` payload:

    {"type":"json_object"}                  -> {"guided_json": {"type":"object"}}
    {"type":"json_schema","json_schema":S}  -> {"guided_json": S}
    {"type":"regex","pattern":P}            -> {"guided_regex": P}
    {"type":"choice","options":[...]}       -> {"guided_choice": [...]}

That was correct for the vLLM 0.4-era API. It is not correct for the version we
deploy, and the failure is silent.

**`guided_json` does not exist in vLLM 0.27.1.** Verified against the installed
package rather than the docs: `grep -rl guided_json` over the whole distribution
returns **zero** hits. The API is now `StructuredOutputsParams`, exposed on the
OpenAI-compatible endpoint as `structured_outputs`.

**The unknown field is accepted and dropped.** `OpenAIBaseModel` is
`ConfigDict(extra="allow")`, so `guided_json` passes request validation and is
discarded. The only notice is a `logger.debug` line, invisible at default log
level.

**And the translation removed the thing that worked.** The call site was an
`elif`, so when the translation fired, `response_format` was not set on the
request. vLLM handles `response_format` natively and correctly —
`structured_outputs_from_response_format` maps `json_object` and `json_schema`
itself, including the schema unwrap our translator got wrong (it took
`response_format["json_schema"]`, which is `{"name":..., "schema":...}`: a JSON
Schema with no `type` and no `properties`, matching any JSON — vLLM unwraps one
level deeper).

So on the exact deployment we are targeting, **the translation converted working
structured output into no structured output**.

### The blast radius is wider than one field

Three JSON safety nets gate on `response_format` being present in the request
kwargs, and all three therefore disabled themselves at the same time:

- `_maybe_boost_json_budget` returns early when `response_format` is absent, so
  the doubled-token retry for truncated JSON never ran.
- `_completion_retry_variants` skips the `json_prompt_variant` branch, so the
  `"Return ONLY valid JSON."` fallback was never appended.
- The salvage parser (`extract_json_object`) absorbed the resulting prose and
  counted it as `seocho.gen_ai.structured_output_repair.count`.

The runaway thinking-in-content failure those were written for — measured at
43k-character responses on MiniMax-M2.7 — was therefore *the default* on
self-hosted vLLM, with every mitigation off.

### The repair counter was reading as a model problem

With guided decoding actually on, prose before JSON is untokenizable: the
grammar forbids it. So a non-zero
`seocho.gen_ai.structured_output_repair.count` from a vLLM-served model is not a
model-quality signal at all — it is **a serving-configuration alarm**. It has
been silently absorbing this bug and reporting it as "MiniMax emits
chain-of-thought."

### Capability was keyed on the wrong thing

`capability_for()` resolved a profile from the **model name**, first-match-wins.
Its `vllm` entry matches the literal string `vllm`, which is never a served
model name. A self-hosted `MiniMax-M2.7` matched the `minimax` entry first and
inherited `supports_guided_json=False`.

So the one deployment where schema enforcement is *exact* — vLLM constrains the
decoder with a grammar — was the one deployment that could never reach it.

## Decision

**Delete the translation.** Pass `response_format` through unchanged to every
provider, vLLM included. This is a net deletion and it fixes the dropped field,
the wrong schema level, and the three disarmed retries at once.

**Make capability a function of `(server, model)`.** `capability_for(model,
provider)` upgrades `supports_guided_json` when the *server* enforces a schema in
the decoder, regardless of which model is loaded. `_SCHEMA_ENFORCING_PROVIDERS`
currently holds `vllm`. A hosted OpenAI-compatible endpoint that merely accepts
the parameter (MARA) stays conservative and keeps `json_object` plus robust
parsing.

Measured after:

| model | provider | `supports_guided_json` |
| --- | --- | --- |
| MiniMax-M2.7 | *(none)* | False |
| MiniMax-M2.7 | `vllm` | **True** |
| MiniMax-M2.7 | `mara` | False |
| gpt-4o | *(none)* | True |

## Consequences

- Structured output works on self-hosted vLLM. Every extraction and text2cypher
  quality number measured against a vLLM endpoint before this change was
  measured with structured output off, and should be re-run.
- `seocho.gen_ai.structured_output_repair.count` becomes meaningful for vLLM: a
  non-zero rate now indicates a serving misconfiguration, not model behaviour.
  It is worth alerting on for that reason.
- Agent mode is unchanged. ADR-0098's rule that the Agents SDK's tool-call
  structure supersedes `response_format` still holds, and no `response_format`
  is injected on that path.
- One risk to name: a vLLM version that *stops* honouring `response_format` would
  now fail open rather than being caught by our translation. The mitigation is a
  live smoke assertion against a running server — post a deliberately
  schema-violating request and assert a 400 or a constrained response — in the
  shape of `scripts/serve_track/smoke_plugin.py`. Tracked separately; the unit
  tests here pin the request shape but cannot see a server-side rename.

## What was NOT changed

ADR-0098's vLLM provider preset (`base_url`, blank `default_model`, optional API
key), its agent-mode handling, and the `seocho-vllm-probe` stat-logger plugin
distribution are all unaffected and remain in force.
