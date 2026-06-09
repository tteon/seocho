# Prompt Assembly Discussion Memo

Date: 2026-05-24
Status: Discussion memo, not an ADR
Owner: `seocho-futd`

## Why This Memo Exists

SEOCHO now has working provider routing across OpenAI, DeepSeek, Kimi, and
Grok, but prompt quality and latency still depend heavily on how dynamic
context is ingested and combined per stage.

The current question is not whether prompts matter. They do.

The real design question is:

`What should stay code-owned and what should become declarative in SEOCHO's prompt assembly layer?`

This memo is the team discussion frame for that question. It is intentionally
not a final decision record.

## Existing Constraints

Any solution needs to respect the repository's existing product and
architecture commitments:

- ontology-governed behavior, not hidden prompt drift
- one semantic control plane for both indexing and query
- `prompt_context` as an override layer, not the primary governance path
- provider quirks isolated to adapter/runtime settings
- stable prompt prefixes preserved where caching matters

Supporting references:

- [docs/SEMANTIC_CONTROL_PLANE.md](SEMANTIC_CONTROL_PLANE.md)
- [docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md](GRAPH_RAG_AGENT_HANDOFF_SPEC.md)
- [docs/BASELINE_INSTRUCTIONS.md](BASELINE_INSTRUCTIONS.md)
- [docs/decisions/ADR-0029-typed-semantic-prompt-context-and-artifact-expert-surface.md](decisions/ADR-0029-typed-semantic-prompt-context-and-artifact-expert-surface.md)

## Working Consensus

The four review perspectives converged on the same position:

1. SEOCHO should not make raw prompt strings the primary open-source contract.
2. SEOCHO should not expose a prompt DSL with unconstrained override freedom.
3. Prompt assembly should be stage-aware and typed.
4. Provider-specific behavior should stay in adapter code such as
   [src/seocho/store/llm.py](../src/seocho/store/llm.py).
5. The public seam should remain ontology- and artifact-centric.

## Proposed Boundary

### Keep Hard-Coded In Core Code

- typed semantic models such as `SemanticPromptContext`
- semantic precedence and merge rules
- stage taxonomy
- output and safety invariants
- cache-stable prompt block ordering
- provider capability adapters and transport fallbacks

### Make Declarative

- stage prompt text
- stage template selection
- ontology/vocabulary overlays
- indexing and agent design overlays
- model/provider selection where already supported

### Keep Internal Or Unstable

- exact internal prompt wording
- raw prompt assembly order details
- provider-specific workaround copy
- escape-hatch prompt injection APIs unless later promoted into a typed contract

## Stage-Aware Design Direction

The recommended shape is a shared typed prompt IR, not a universal raw string
template.

Suggested flow:

`ontology/artifacts + request prompt context + runtime metadata -> stage-aware prompt IR -> provider adapter -> request payload`

The shared layer should own:

- source precedence resolution
- section selection and truncation
- stable prefix construction
- structured receipts for testing and tracing

Stage-specific code should still own:

- task instructions
- output schema
- retry policy
- evidence policy
- reasoning on/off decisions

## Proposed Module Ownership

- `seocho/*`
  - canonical owner of semantic compilation and prompt composition
- `runtime/*`
  - orchestration and transport shell only
- `seocho/store/llm.py`
  - provider request shape, fallback, and reasoning/tool quirks
- `extraction/*`
  - compatibility shims or legacy wrappers where still needed

This matches the repo's broader direction that `seocho/*` owns canonical logic
and `runtime/*` owns deployment shell behavior.

## Migration Slices

### Slice 0: Preparation

- add a typed internal prompt IR and receipt scaffold
- document the discussion frame and boundaries
- add tests for precedence and stable-prefix behavior

### Slice 1: Canonical Composer

- introduce a canonical prompt composer module under `seocho/*`
- keep `extraction/semantic_context.py` as a shim over that composer
- keep behavior unchanged while centralizing merge logic

### Slice 2: Stage Compilation

- move extraction, linking, planner, and answer synthesis toward shared stage IR
- preserve stage-specific system rules and output contracts
- keep reasoning policy stage-local

### Slice 3: Cross-Surface Convergence

- route ingest and query through the same semantic prompt assembly contract
- keep provider-specific request shaping in `LLMBackend`
- add parity and eval coverage around prompt receipts and stage behavior

## Open Questions For Team Discussion

1. Should raw prompt override remain a supported advanced seam or be explicitly
   marked unstable?
2. How coarse should the first stage taxonomy be?
   - broad stages such as extraction/linking/planner/answering
   - or current concrete lanes such as ontology candidate, SHACL candidate,
     Graph-CoT supervisor, and guardrail review
3. Should prompt templates live in YAML only, code only, or both?
4. How much of prompt receipt data should surface in traces or public metadata?
5. When does a prompt/text change require an ADR versus a normal code review?

## Implementation Readiness

The first implementation slice should not change user-visible behavior.

It should only establish:

- a typed prompt IR
- a typed assembly receipt
- fixed semantic precedence ordering
- tests that verify the contract without snapshotting full prompt text

That gives later slices a stable internal seam without prematurely freezing the
full prompt architecture as public API.
