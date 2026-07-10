# Build an observable OKX-style agent-memory vertical slice

This ExecPlan is a living document. The sections `Progress`, `Surprises &
Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to
date as work proceeds.

This plan follows `.PLANS.md` from the repository root and tracks local work
item `seocho-47m`.

## Purpose / Big Picture

After this change, a developer can run an OKX-style customer-support query
through SEOCHO and inspect one portable trace that explains prompt identity,
provider usage, graph-query work, evidence coverage, tenant scope, and failure
state. The first supported query family is withdrawal explanation: a user asks
why a crypto withdrawal is pending or blocked, and the system constructs a
typed, bounded evidence request without allowing an LLM to authorize a
financial action.

The work intentionally starts with measurement. FoundationDB-backed durable
shared memory, etcd coordination, and LiteLLM gateway deployment are later
milestones. They will not be introduced until the current runtime can measure
their latency, cost, consistency, and failure effects.

## Progress

- [x] (2026-07-10) Created local work item `seocho-47m` and an isolated
  `feat/okx-agent-memory` worktree from current `main`.
- [x] (2026-07-10) Located the prior OTel implementation in
  `feat/observability-otel` and confirmed it cannot be merged wholesale because
  it includes hundreds of unrelated later commits.
- [x] (2026-07-10) Ported a minimal OTLP backend into the current vendor-neutral tracing
  contract with no mandatory dependency or hosted backend.
- [x] (2026-07-10) Defined typed OKX query-family and prompt identity contracts for withdrawal
  explanation.
- [ ] Instrument prompt assembly, provider invocation, graph query, evidence
  selection, and agent exchange with privacy-safe attributes.
- [ ] Partially complete: QueryProxy emits privacy-safe `db.query` spans.
  Provider, evidence-selection, and agent-exchange spans are now implemented;
  native OTel span links and live service validation remain.
- [x] (2026-07-10) Added deterministic tests for tenant scope, content
  redaction, prompt versioning, missing evidence, and trace shape.
- [x] (2026-07-10) Added approved, parameterized Cypher compilation for the
  withdrawal workload and a bounded one-repair Text2Cypher fallback policy.
- [x] (2026-07-10) Added Mara/OpenAI-compatible `gen_ai.chat`, evidence bundle,
  and typed agent-exchange telemetry with content capture off by default.
- [x] (2026-07-10) Ran 18 focused tests plus basic CI (631 passed, 3 skipped);
  recorded live-MARA and live-Collector validation gaps.

## Surprises & Discoveries

- Observation: The current `main` checkout has vendor-neutral JSONL/Opik
  tracing but no OTel backend, while the separate `feat/observability-otel`
  worktree contains a substantial implementation.
  Evidence: `rg "opentelemetry|SEOCHO_TRACE_OTLP_ENDPOINT"` is empty on current
  `main` and finds `OTLPBackend` plus ADR-0144 on the feature worktree.

- Observation: The OTel branch is a direct descendant of current `main`, but
  the OTel commits sit after hundreds of unrelated commits.
  Evidence: `git merge-base main feat/observability-otel` equals current main
  HEAD while the feature branch is 621 commits ahead. The OTel code must be
  adapted, not merged wholesale.

- Observation: Current isolated pytest creation used Python 3.14 while pytest
  loaded from a Python 3.10 user installation, producing an existing
  `asyncio_mode` warning. A broad internal-design test also needs repository
  root on `PYTHONPATH` to import `runtime`; the focused changed tests pass.
  Evidence: OTel/workload tests report 12 passed, one optional-exporter skip;
  QueryProxy tests report 6 passed.

## Decision Log

- Decision: Keep DozerDB as the graph baseline and exclude Memgraph from this
  implementation.
  Rationale: The useful ideas are workload isolation, multi-hop measurement,
  and per-query observability; adding another graph engine does not prove those
  properties.
  Date/Author: 2026-07-10 / Codex

- Decision: Treat OTel as an exporter of SEOCHO's vendor-neutral trace contract,
  not as the product trace contract itself.
  Rationale: JSONL remains replayable and portable, while OTLP supplies
  cross-process traces and metrics.
  Date/Author: 2026-07-10 / Codex

- Decision: Treat LiteLLM as an optional model gateway, not an agent messaging
  bus.
  Rationale: Agents exchange typed memory and evidence references. LiteLLM may
  later provide Mara routing, budgets, retries, and usage accounting without
  changing agent contracts.
  Date/Author: 2026-07-10 / Codex

- Decision: Implement one customer-query family before distributed storage.
  Rationale: A real query contract creates measurable prompt, graph, evidence,
  and privacy requirements that can justify later infrastructure.
  Date/Author: 2026-07-10 / Codex

- Decision: Use approved Cypher recipes before free-form Text2Cypher.
  Rationale: Known customer workloads need deterministic tenant scope and
  bounded reads. Mara extracts typed slots; only unknown families enter a
  schema-constrained, `EXPLAIN`-first fallback with one repair attempt.
  Date/Author: 2026-07-10 / Codex

## Outcomes & Retrospective

The first two milestones now provide an optional OTLP exporter, backend-neutral
nested spans, default-off content capture, a typed withdrawal workload, and a
privacy-safe `db.query` span. Approved recipes now precede bounded Text2Cypher;
Mara/provider, evidence-selection, and typed agent-exchange spans are present.
Focused slice validation passed 19 tests after the second milestone and basic
CI again passed 631 tests with three skips. Live Mara, a live OTel Collector,
native OTel span links, and durable shared memory remain follow-up work.

## Context and Orientation

`src/seocho/tracing.py` owns the SDK trace backend abstraction. JSONL is the
canonical portable artifact and Opik is an optional exporter.
`src/seocho/store/llm.py` owns OpenAI-compatible providers including Mara.
`src/seocho/query/query_proxy.py` is the canonical guarded graph-query seam.
`src/seocho/query/` owns intent, evidence, routing, and answer orchestration.
`runtime/` is the deployment shell and must preserve `workspace_id` and policy
checks. New canonical behavior belongs under `src/seocho/`, not `extraction/`.

A query family is a stable class of customer question with named input slots,
required evidence, allowed tools, and a latency/safety policy. A causal token
is a future shared-memory commit identifier that lets a later request demand at
least the state observed by an earlier request. It is represented in telemetry
now but durable enforcement is deferred.

## SEOCHO Evidence Contract

The withdrawal-explanation query family requires slots for workspace,
withdrawal identifier, asset, network, destination category, account state,
network state, restriction state, and applicable policy. It may return grounded
facts and missing slots, but it may not claim that a withdrawal is authorized.

The evidence bundle must preserve `intent_id`, required relations, candidate
entities, selected triples, slot fills, missing slots, provenance, confidence,
database identity, ontology hash, and support status. OTel records counts,
versions, hashes, and statuses by default. It does not record prompts, wallet
addresses, customer names, API credentials, or raw financial payloads.

## SEOCHO Review Panel

The `professor_agent` lens favors a query-family benchmark over a generic graph
speed claim because the semantic question is whether structured context improves
grounded explanation and refusal. The falsifier is a fair fixed-answerer test
where the graph lane does not improve provenance or missing-slot behavior.

The `software_engineer_agent` lens requires typed contracts and optional
dependencies. The OTel exporter must not change behavior when it is disabled,
and tests must run without a collector or Mara key. The falsifier is an import
or runtime regression in the thin client installation.

The `computer_systems_agent` lens requires span topology, token/cost fields,
graph latency, and projection-lag placeholders before adding FoundationDB,
etcd, or a gateway. The falsifier is telemetry overhead large enough to affect
the measured query path or high-cardinality attributes that make the backend
unsafe.

The panel decision is to proceed with the observable vertical slice and defer
new distributed services.

## Cost, Latency, and Provider Policy

Focused tests use no network. A later opt-in integration test uses Mara through
the existing OpenAI-compatible backend. LiteLLM is not a code dependency in
this milestone. Provider spans record provider, model, prompt version, input
tokens, output tokens, duration, retry count, and cache status when available.

Raw content capture is off by default. Workspace identifiers and customer-linked
values are hashed or omitted. The benchmark reports cost per supported answer,
p50/p95 latency, invalid-query rate, evidence coverage, missing-slot rate, and
unsupported-claim rate.

## Plan of Work

First adapt the minimal OTLP backend, nested span support, privacy-safe attribute
flattening, and optional dependencies from the older OTel branch into current
`src/seocho/tracing.py`. Add focused tests under `tests/seocho/` and document the
decision in ADR-0144 without copying unrelated later architecture.

Next add an OKX workload module under `src/seocho/query/` containing typed query
family, prompt identity, evidence requirements, and safety policy objects. Add
a small public example dataset containing synthetic, non-customer withdrawal
cases and deterministic expected slots.

Then instrument the existing query and LLM seams. The implementation uses
OTel-style semantic names where stable and `seocho.*` attributes for project
contracts. Agent exchange uses span links when a backend supports them and
preserves trace identifiers in the typed envelope.

Finally run the focused suite and basic CI. Live Mara and Collector tests are
explicit opt-in validations and must self-skip without credentials or services.

## Concrete Steps

From the repository root:

    uv run pytest tests/seocho/test_tracing_otel.py -q
    uv run pytest tests/seocho/test_okx_query_workloads.py -q
    uv run pytest tests/seocho/test_query_proxy.py -q
    uv run python -c "import seocho; print(seocho.__file__)"
    bash scripts/ci/run_basic_ci.sh

When a Mara key is present, run the later opt-in integration scenario with an
explicit model and no financial action tool:

    MARA_API_KEY=... uv run pytest -m integration \
      tests/seocho/test_okx_mara_agent_exchange.py -q

## Validation and Acceptance

The slice is accepted when importing `seocho` remains possible without OTel,
enabling the OTLP backend produces nested spans through a fake or in-memory
exporter, default traces contain no raw prompt or customer content, and the
withdrawal workload exposes missing evidence rather than fabricating a reason.

The deterministic query-family tests must show mandatory workspace scoping,
bounded graph hops, parameterized query inputs, a stable prompt version, and a
policy that forbids the model from authorizing or executing a withdrawal.

Basic CI must pass. Any live-service test not run is recorded as a validation
gap rather than reported as success.

## Idempotence and Recovery

All implementation occurs in the isolated `/tmp/seocho-okx` worktree. The
user's dirty `main` worktree is never reset, restored, or staged. Test commands
are safe to repeat. OTel dependencies are optional and disabled by default.
Synthetic benchmark artifacts use ignored output paths.

If the older OTel code does not fit current interfaces, port behavior in small
patches rather than cherry-picking unrelated history. A failed exporter must
degrade observability only; it must not fail the customer request.

## Artifacts and Notes

The prior implementation is available for reference at
`/home/hadry/lab/seocho-obs-otel`. Its relevant commits begin with ADR-0144 and
then add OTLP backend, nested RAG spans, graph-query spans, a local collector
stack, prompt identity, and governance metrics. These commits are reference
material, not merge inputs.

## Interfaces and Dependencies

The tracing module will support `otlp` alongside existing backends. OTel SDK
and OTLP gRPC exporter packages live in an optional `otel` dependency group.
The public trace contract remains backward compatible.

The query workload module will expose immutable typed objects for
`PromptIdentity`, `QueryFamilySpec`, `EvidenceRequirement`, and
`QuerySafetyPolicy`. The first catalog entry is `withdrawal_explanation.v1`.
It must be usable without FastAPI, a graph connection, or a provider key.

Revision note (2026-07-10): Initial plan created after auditing current main,
the dirty user worktree, and the older OTel feature worktree. The scope was
narrowed to observability plus one workload before distributed-memory services.

Revision note (2026-07-10): Updated after the first implementation milestone.
Recorded the OTLP/workload/query-span behavior and exact focused/basic-CI
results; kept live provider and distributed-memory work explicitly open.

Revision note (2026-07-10): Added tiered Cypher compilation, provider/evidence/
exchange telemetry, and a second successful basic-CI run. Native span links and
live-service validation remain explicit gaps.
