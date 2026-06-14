# ADR-0120: Provider/Model-Aware Structured-Output Layer (seocho-ub5)

Date: 2026-06-14
Status: Proposed

## Context

Every LLM-driven evidence step in the ontology pipeline (OntoClean meta-property
inference, open/guardrail extraction) needs reliable JSON across providers. The
FinDER/OntoClean MARA runs showed a single prompt + `response_format` is not
portable: DeepSeek-V3.1 returned clean JSON, but **MiniMax-M2.x and gpt-oss-120b
emit chain-of-thought** and broke naive parsing (MiniMax-M2.7 ~16-20% failures in
the big ablation), and **gpt-oss returned empty output at a low `max_tokens`**
because reasoning consumed the budget. Losing a model to parse failure silently
shrinks ensemble coverage.

## Decision

Add `seocho.llm_structured` — a provider/model-aware layer above the backend
(which already translates `response_format` to vLLM guided decoding, ADR-0098):

- **`ModelCapability` registry** keyed by model name (one provider serves many
  models): `emits_reasoning`, `supports_json_object`, `supports_guided_json`,
  `max_tokens_floor`, `temperature_clamp`. Known families: deepseek (clean),
  minimax / gpt-oss / deepseek-reasoner (reasoning, 4096 floor), openai/vllm
  (guided JSON), kimi (temp clamp 1.0); unknown names matching `think|reason|-r1`
  get a reasoning profile, else a conservative default.
- **`structured_complete(backend, …, schema=, model=)`** — picks the best
  strategy the model supports (json_schema/guided → json_object → prompt-injected),
  raises the `max_tokens` floor, applies the temperature clamp, appends an "emit
  ONLY the final JSON" instruction for reasoning models, and parses robustly.
- **`extract_json_object()`** — strips `<think>` blocks and code fences, tries a
  direct parse, else returns the **largest balanced `{...}`** that parses (so an
  echoed example never beats the real payload). Generalized from the
  OntoClean-local helper, which now delegates here.

`ontology_ontoclean.infer_metaproperties` is routed through `structured_complete`.
Offline/data-plane; composes with `store/llm.py`; no hot-path reasoning.

## Validation (measured 2026-06-14)

Parse-success rate, 20 FinDER docs/model, naive (`json_object`, `max_tokens=512`,
`json.loads`) vs `structured_complete`. Record: `ADR-0120-ub5-parse-success.json`.

| model | naive | structured |
|---|---|---|
| gpt-oss-120b | **45%** | **100%** |
| MiniMax-M2.7 | 95% | 100% |
| DeepSeek-V3.1 | 95% | 100% |

The structured layer brings **all** models to 100% parse success; the dramatic
win is gpt-oss (+55pp) — at `max_tokens=512` its reasoning ate the budget and
left no JSON; the floor + "JSON-only" suffix fix it. (Failure magnitude varies
with doc length/task — M2.7's ~16-20% appeared on the longer guardrail-ablation
prompts — but the layer robustly wins regardless.)

## Consequences

- Ensembles no longer lose reasoning models to parse failure → OntoClean tagging
  and extraction are portable across MARA's DeepSeek/MiniMax/gpt-oss and beyond.
- One chokepoint for structured output → future models are onboarded by adding a
  registry row, not by editing every caller.
- Follow-ups: meta-prompt templates per family as files (precedent:
  `examples/finder/datasets/{grok,kimi}_meta_system_prompt.md`); verify
  json_schema/guided support on MARA to upgrade those models from json_object.
