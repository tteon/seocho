# ADR-0148: Cache-Aware SEOCHO Prompt Package

Date: 2026-07-12
Status: Proposed

## Context

SEOCHO can call hosted APIs through Mara or a gateway and can also serve models
with vLLM or SGLang. Provider names are not sufficient to describe prompt
behavior: two OpenAI-compatible endpoints may differ in cache keys, explicit
cache controls, structured output, system-role handling, and usage reporting.

Agent-memory prompts also combine content with very different lifetimes. The
ontology, disclosure policy, tool contract, and output schema change rarely;
session memory changes occasionally; retrieved evidence and the question change
on every request. Mixing these sections or injecting timestamps near the front
destroys prefix reuse and makes prompt changes difficult to audit.

## Decision

`StagePromptSpec` is the provider-neutral `seocho.prompt.v1` package. Each
section has a stable ID, source, kind, content hash, stability class
(`immutable`, `workspace`, `session`, or `request`), sensitivity flag, and
cache scope (`workspace`, `session`, or `none`). Stable sections precede
volatile sections. Receipts contain hashes, counts, scopes, and versions but
never prompt bodies.

Rendering is capability-driven. Built-in profiles cover generic
OpenAI-compatible endpoints, Mara, OpenAI, xAI/Grok, Kimi, Qwen, Meta Muse,
vLLM, SGLang, and Anthropic. Deployments can override a profile without
changing the package. Anthropic receives an explicit cache breakpoint; vLLM
can receive a tenant cache salt; xAI can receive a prompt cache key; automatic
cache backends receive byte-stable ordered messages.

Sensitive content cannot enter a workspace-shared prefix. Cache salts and keys
are hashed in the SEOCHO receipt. They remain transport controls, not access
control. A gateway must preserve provider-specific fields or explicitly report
that it removed them.

## Canonical order

1. task and safety contract
2. approved ontology/schema and disclosure policy
3. tool definitions, query hints, and output contract
4. session memory summary
5. retrieved evidence and execution diagnostics
6. current user request

Items 1–3 are normally workspace-stable, item 4 is session-scoped, and items
5–6 are request-scoped. Canonical serialization must exclude timestamps,
random request IDs, and nondeterministic map/set ordering from the prefix.

## Measurement and rollout

Evaluate by backend and model with cold/warm paired requests. Record prefix
token estimate, provider-reported cached tokens, cache hit/miss/unknown, TTFT,
prefill latency when available, end-to-end latency, input/output tokens, cost,
and answer-quality gates. Prefix caching is accepted only when quality and
governance results are unchanged and warm TTFT or input cost improves.

Do not infer hits from latency alone. API providers may not expose a reliable
hit field, and prefix caching accelerates prefill rather than output decoding.
The initial implementation defines and validates the format; live
provider/model benchmarks are a separate evidence artifact.

## Consequences

Customers can add Spark/Muse, Grok, OpenAI, Kimi, Qwen, or a private endpoint
through a capability profile instead of forking prompt composition. Stable
prefix changes are auditable by hash and ontology version. The extra section
metadata and adapter tests are intentional; a cross-provider lowest-common-
denominator prompt is rejected because it hides material runtime differences.

