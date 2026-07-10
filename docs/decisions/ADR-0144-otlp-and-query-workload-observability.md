# ADR-0144: OTLP and Query-Workload Observability

Date: 2026-07-10
Status: Proposed

## Context

ADR-0045 made JSONL the canonical vendor-neutral trace artifact and Opik an
explicit optional backend. SEOCHO now needs to measure external-API prompt
behavior, graph-query latency, evidence coverage, and distributed agent work
without requiring the large self-hosted Opik stack. The first target workload
is an OKX-style withdrawal explanation, where tenant isolation and omission of
financial or identity content from telemetry are safety requirements.

An older `feat/observability-otel` worktree proved a local OTLP backend, but it
also contains hundreds of unrelated changes. This ADR adopts only the tracing
primitives that fit current main and makes workload-driven instrumentation the
gate for later FoundationDB, etcd, and LiteLLM deployment work.

## Decision

Add `otlp` as an optional exporter behind the existing `TracingBackend`
contract. `opentelemetry-sdk` and the OTLP gRPC exporter remain an optional
`seocho[otel]` install. JSONL remains canonical and Opik behavior is unchanged.

Add nested `start_span()` support shared by all backends. OTLP uses native
parent/child spans; flat backends receive trace and parent identifiers in their
metadata. Export failures never fail the business request.

Raw prompt, completion, Cypher, retrieved content, wallet, account, and user
data are absent by default. `SEOCHO_TRACE_CAPTURE_CONTENT=1` is an explicit
debug-only opt-in with truncation. Normal spans carry hashes, versions, counts,
durations, route decisions, evidence status, and missing-slot names.

Define versioned query-family contracts before optimizing prompts. The first
family, `withdrawal_explanation.v1`, is read-only, workspace-scoped, bounded to
four graph hops, and prohibited from authorizing or executing a withdrawal.
Its prompt identity and evidence requirements are observable without recording
the supplied values.

Instrument `QueryProxy` with a `db.query` span containing database identity,
query-template hash, workspace hash, workspace-filter status, row count, and
duration. Full Cypher is content-gated.

Compile known query families with approved, parameterized Cypher recipes. An
LLM extracts typed slots but does not generate the query for these families.
Unknown families may use schema-constrained Text2Cypher with mandatory
`workspace_id`, read-only validation, a four-hop and 50-row bound, `EXPLAIN`
before execution, and at most one repair attempt.

Instrument OpenAI-compatible provider calls, including Mara, with content-free
`gen_ai.chat` spans by default. Agent-to-agent handoffs exchange typed memory
and evidence references; telemetry records counts and hashes, not referenced
contents. Evidence-bundle construction emits coverage, missing-slot,
provenance-count, and support-status signals.

## Consequences

Prompt and query changes can be compared by cost, latency, trace shape, and
grounding rather than by subjective prompt inspection. The same instrumentation
can later measure a Mara call through direct OpenAI-compatible access or a
LiteLLM proxy without making LiteLLM an agent messaging system.

The optional OTel surface adds code and semantic-convention maintenance. GenAI
conventions may evolve, so SEOCHO-owned stable attributes remain under the
`seocho.*` namespace. Hashes reduce exposure but do not replace access control
or Collector-side redaction.

Memgraph, Kafka, Neo4j federation, and a self-hosted observability stack are not
introduced. FoundationDB shared memory and etcd worker coordination remain
follow-up decisions after their expected effects can be measured.

## Validation

- Importing SEOCHO without OTel installed succeeds.
- Focused OTel tests pass without a live Collector.
- Default query spans contain no raw workspace, customer, wallet, or Cypher
  content.
- Content capture requires explicit opt-in.
- The withdrawal workload exposes prompt version, required slots, missing
  slots, bounded traversal, and forbidden actions deterministically.
- Existing workspace-enforcement tests remain green.
