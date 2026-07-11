# Build an observable OKX-style agent-memory vertical slice

This ExecPlan is a living document. The sections `Progress`, `Surprises &
Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to
date as work proceeds.

This plan follows `.PLANS.md` from the repository root and tracks local work
item `seocho-47m`.

## Purpose / Big Picture

The consolidated implementation and evaluation contract now lives in
`docs/OKX_AGENT_MEMORY_SYSTEM_SPEC.md`. This ExecPlan retains the chronological
record of the initial vertical slice; new milestones should use the system
spec's P0-P6 checklist and Q1-Q12 evaluation matrix.

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

- [x] (2026-07-11) Started P1 with an optional-psycopg PostgreSQL repository
  that atomically allocates a workspace sequence and writes a memory revision,
  idempotency receipt, and projection outbox entry.
- [x] (2026-07-11) Defined the Neo4j/DozerDB TLS observability contract:
  native Prometheus scraping plus low-frequency certificate expiry,
  handshake, and reload probes correlated with graph request traces.
- [x] (2026-07-11) Added the consolidated system specification, PostgreSQL v1
  authoritative-memory schema, typed context/usage/answer receipts, and a
  deterministic single-user longitudinal generator with Q1-Q12 gold queries.
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
- [x] (2026-07-11) Added `transaction_risk_preflight.v1`, a bounded four-hop
  wallet-risk recipe, deterministic risk disposition, etcd-safe coordination
  pointers, and role/subject-aware ontology disclosure filtering.
- [x] (2026-07-11) Ran 24 focused risk/workload tests and basic CI after the
  risk milestone (631 passed, 3 skipped).
- [x] (2026-07-11) Implemented and validated the blockchain long-term-memory
  contract and public-data benchmark. The milestone includes
  versioned canonical events, idempotent block ingestion, reorg revisions,
  transactional risk aggregates/outbox, and causal projection watermarks.
- [x] (2026-07-11) Ran 34 focused memory/risk/query tests, repeated the live
  OFAC/Bitcoin benchmark, and passed basic CI (631 passed, 3 skipped).
- [x] (2026-07-11) Added a six-case Mara risk-preflight E2E dataset and
  provider runner; no-key execution reports an explicit skip and never sends
  unfiltered evidence.
- [x] (2026-07-11) Added the integrated public-chain → long-term-memory →
  approved-query → disclosure → concurrent-Mara vertical-slice runner and
  verified it against two real blocks.
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

- Decision: Use Beads only as the local coordination ledger for multi-step,
  dependency-bearing, or parallel agent work; keep GitHub issues/PRs as the
  canonical public record and this ExecPlan/system spec as the durable design
  narrative.
  Rationale: Beads is useful for claim, ready/blocked, dependency, and handoff
  state, but duplicating every small edit creates stale planning state and
  worktree overhead. The OKX memory P1-P6 milestones qualify; single-file and
  same-session changes do not need separate Beads items.
  Date/Author: 2026-07-11 / User and Codex

- Decision: Share one Beads workspace across the OKX feature worktrees rather
  than initializing independent `.beads` histories in each worktree.
  Rationale: Separate trackers would fork coordination truth. Before relying
  on Beads gates, align the CLI/hooks and verify the shared workspace with
  `bd doctor` and sandboxed reads.
  Date/Author: 2026-07-11 / User and Codex

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

- Decision: Do not store user or wallet risk data in etcd.
  Rationale: etcd coordinates active policy, worker ownership, fencing, and
  projection progress. Authoritative customer/risk history belongs in durable
  memory and graph projections; this avoids turning the coordination plane into
  a sensitive operational database.
  Date/Author: 2026-07-11 / Codex

- Decision: Enforce disclosure before prompting.
  Rationale: Ontology property classifications compile to deterministic
  role/subject filtering. Prompts explain already-filtered evidence and cannot
  grant access to restricted properties.
  Date/Author: 2026-07-11 / Codex

- Decision: Treat the blockchain event log as authoritative and the graph as a
  rebuildable serving projection.
  Rationale: Reorganizations and policy changes require append-only revisions
  and replay. Mutating graph edges in place would lose the evidence needed to
  explain or roll back an earlier risk decision.
  Date/Author: 2026-07-11 / Codex

- Decision: Keep the FoundationDB client optional behind a transaction-runner
  boundary and validate semantics with a deterministic in-memory runner.
  Rationale: The Python binding requires a matching native client and live
  cluster. SEOCHO's default import and CI must remain usable without either,
  while the same key layout and transaction function can run on FoundationDB.
  Date/Author: 2026-07-11 / Codex

## Outcomes & Retrospective

The first two milestones now provide an optional OTLP exporter, backend-neutral
nested spans, default-off content capture, a typed withdrawal workload, and a
privacy-safe `db.query` span. Approved recipes now precede bounded Text2Cypher;
Mara/provider, evidence-selection, and typed agent-exchange spans are present.
Focused slice validation passed 19 tests after the second milestone and basic
CI again passed 631 tests with three skips. Live Mara, a live OTel Collector,
native OTel span links, and durable shared memory remain follow-up work.

The risk-preflight milestone adds a second measurable workload without adding
a live etcd dependency. It proves the storage boundary with coordination
record validators, creates a parameterized hop-bounded query, fails stale
projections to review, and strips restricted evidence before synthesis. Its 24
focused tests pass, as does basic CI with 631 passed and three skipped. This is
a contract-level vertical slice: live etcd, FoundationDB-backed authoritative
memory, graph ingestion, and a live Mara/LiteLLM integration remain explicit
follow-up work.

The first public-data memory run read the current OFAC SDN XML and Blockstream
Esplora mainnet API. It found 518 XBT-labelled seeds; a bounded one-address run
fetched two confirmed transactions and derived 102 address-interaction events
across two blocks. The reference runner created 102 outbox entries and treated
both complete block replays as no-ops. Its approximately 7,907 events/second is
an in-memory contract measurement, not a FoundationDB performance result.

The long-term-memory milestone now has a lazy official-FDB transaction runner,
an atomic in-memory reference runner, append-only canonical/orphan revisions,
risk aggregate compensation, projection outbox entries, and causal watermark
checks. Thirty-four focused tests pass and final basic CI passes 631 tests with
three skips. Live FoundationDB cluster behavior, versionstamp-based sequence
allocation, partition manifests above 128 events, and live DozerDB projection
remain explicit scale gaps.

The LLM E2E dataset is intentionally small and contract-focused rather than a
claim of production answer quality. It fixes disposition and provenance gold,
checks forbidden-field leakage, and leaves model judgment to the opt-in Mara
run. The current environment has no `MARA_API_KEY`, so only the safe skip path
was executed here.

The integrated live run later used the configured Mara `gpt-oss-120b` endpoint:
two real transactions became 102 events, six approved recipes were built, and
six concurrent explanations completed with disposition/provenance accuracy
1.0, zero leakage, and approximately 2.69 seconds p95. This is an end-to-end
smoke result; sustained FoundationDB/DozerDB load and streaming token latency
remain explicit production gates.

## Context and Orientation

`src/seocho/tracing.py` owns the SDK trace backend abstraction. JSONL is the
canonical portable artifact and Opik is an optional exporter.
`src/seocho/store/llm.py` owns OpenAI-compatible providers including Mara.
`src/seocho/query/query_proxy.py` is the canonical guarded graph-query seam.
`src/seocho/query/` owns intent, evidence, routing, and answer orchestration.
`src/seocho/memory/` owns authoritative versioned blockchain memory and the
transaction boundary used by its in-memory and optional FoundationDB runners.
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

The blockchain-memory milestone adds immutable transaction revisions keyed by
workspace, chain, transaction hash, and event index. One transaction reconciles
a block, marks events from a replaced block orphaned, updates repeated-risk
aggregates, and appends graph projection work. Projection consumers acknowledge
a monotonic causal watermark. A lazy FoundationDB runner uses the official tuple
layer and retrying transaction decorator when its native Python binding is
available; focused tests use the identical transaction function through an
in-memory runner.

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

Revision note (2026-07-11): Added the transaction risk-preflight workload,
coordination-plane validation, deterministic disposition, and ontology-based
subject disclosure guardrails; recorded focused and basic-CI validation.

Revision note (2026-07-11): Began the blockchain long-term-memory milestone
with an authoritative event-log, reorg, outbox, aggregate, and causal-watermark
contract; kept live FoundationDB cluster validation explicit and optional.

Revision note (2026-07-11): Added a bounded live-data evaluation lane using
current OFAC XBT labels and Blockstream Esplora transactions. Reports retain
only opaque address references and avoid wallet-owner attribution.
