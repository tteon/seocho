# OKX-Style Distributed Agent Memory System Specification

Date: 2026-07-11
Status: Implementation baseline
Primary goal: demonstrate production-oriented, long-horizon agent memory,
Graph-RAG, context optimization, reliability, and observability through one
auditable transaction workload.

## 1. Outcome

A developer can load a long transaction history for one synthetic user, run
concurrent agent-to-agent transaction and support workflows, and inspect why
each answer used a particular memory revision, graph projection, ontology
policy, and prompt context. The system must remain correct under duplicate
delivery, concurrent writes, stale projections, retries, worker loss, and
blockchain reorganization.

This is not a wallet-risk classifier. Public labels may be used only as direct
provenance seeds in a bounded demonstration. The primary evaluation concerns
transaction management, memory correctness, scalable retrieval, context
selection, and grounded answer generation.

## 2. Job-requirement traceability

| OKX requirement | SEOCHO proof artifact | Evaluation evidence | Benefit demonstrated |
|---|---|---|---|
| Auditable, versioned, rollback-capable long-term memory | PostgreSQL append-only memory revisions, idempotency receipt, outbox, causal token, reorg/rollback history | replay, rollback, reorg, stale-read, cross-session tests | an agent can explain what it knew at a specific time and safely recover |
| End-to-end GraphRAG and knowledge graph pipeline | ingest normalization, transactional outbox, DozerDB projection, approved Cypher/Text2Cypher, evidence bundle | projection parity, 1-4 hop retrieval, plan/db-hit, grounding tests | graph serving is rebuildable, measurable, and answer-relevant |
| Structured context management | etcd descriptor/policy pointers, Context Envelope, Prompt Assembly Receipt, memory revision selection | long-history selection, token-budget, missing-slot, session-resume tests | long histories remain useful without sending the entire history to the LLM |
| Research translated into production infrastructure | Graph-CoT typed handoffs, bounded repair, optional FDB comparison, GraphScope scale gate | ablation and failure-injection matrix | research ideas are accepted only when they improve measurable outcomes |
| Reliability, observability, governance | OTel Collector, Tempo, Prometheus, Grafana, ontology disclosure, drift gates | concurrent load, leakage, telemetry-overhead, version-drift tests | failures and quality regressions are attributable instead of anecdotal |

## 3. Architecture and ownership

```text
Public chain/API + synthetic user/agent events
                    |
                    v
        normalization + idempotency
                    |
                    v
 PostgreSQL authoritative memory + transactional outbox
         |                         |
         |                         v
         |                  projection workers
         |                         |
         v                         v
 etcd coordination          DozerDB serving graphs
 pointers/leases/           multi-database projection
 fencing/watermarks                 |
         |                         |
         +----------+--------------+
                    v
       Context Envelope + ontology disclosure
                    |
                    v
         Mara MiniMax-M2.7 via LiteLLM-compatible seam
                    |
                    v
 answer + evidence receipt + memory usage receipt

All services -> OTLP -> OTel Collector -> Tempo / Prometheus -> Grafana
```

### 3.1 PostgreSQL: authoritative foundational database

PostgreSQL owns customer-scoped agent memory and transaction truth:

- immutable observation/event body
- append-only memory revisions and supersession links
- canonical/orphaned blockchain status
- agent transaction intent and state-machine transitions
- idempotency receipts
- projection outbox
- memory usage receipts and answer provenance
- prompt/ontology/policy version references

No answer is authoritative merely because it exists in DozerDB. PostgreSQL is
the replay and audit source. FoundationDB remains an optional adapter and
comparison profile; it is not required by the default deployment.

### 3.2 etcd: coordination and compact context descriptors

etcd may store only bounded operational metadata:

- active policy, ontology, and prompt-version pointers
- worker lease, shard ownership, and fencing token
- projection watermark and deployment generation
- compact context descriptor pointing to durable memory

It must not store prompts, transaction bodies, user profiles, wallet history,
risk aggregates, or retrieved memory content.

### 3.3 DozerDB: graph serving and database isolation

DozerDB serves rebuildable projections optimized for:

- direct transaction lookup
- temporal transaction neighborhood
- agent/user/counterparty relationships
- bounded one-to-four-hop paths
- memory-to-decision provenance
- database-scoped workload isolation

Multi-database queries use application-level bounded fan-out with per-target
timeouts, partial-result status, and one merged evidence bundle. Cross-instance
native federation is not assumed.

### 3.3a PostgreSQL SQL/PGQ versus native graph hypothesis

The Memgraph article "PostgreSQL 19 vs Memgraph: Comparing Graph Traversal
Performance" is retained as a vendor-authored benchmark hypothesis, not as
SEOCHO evidence. It reports exact outbound N-hop reachability over Pokec medium
(100,000 vertices and 1,768,515 edges), one-to-five hops, warmup before timing,
and a 30-second timeout. The reported boundary appears at deeper hops, but the
comparison uses Memgraph-specific loading, indexes, query syntax, and execution
modes and therefore cannot establish DozerDB performance.

SEOCHO may use the Pokec scripts only to inspect benchmark mechanics. All
architecture evaluation and published scorecards use the longitudinal Bitcoin
transaction-memory graph, including real public transaction shapes and
synthetic user/agent bindings. The purpose is to test
the architecture boundary:

- PostgreSQL remains authoritative regardless of traversal latency.
- DozerDB remains justified only when measured retrieval latency, throughput,
  or query-plan stability improves the online agent workload enough to offset
  projection lag and operational cost.
- One-hop parity must not be generalized into a deep-traversal claim.
- A native-graph win must not be generalized into write, audit, rollback, or
  point-in-time-memory superiority.

Fairness controls are identical blockchain vertex/edge sets, start
transactions or opaque address references, direction,
exact-hop semantics, distinct-count semantics, warmup, repetitions, timeout,
container resources, cold/warm reporting, indexes, and result parity. Record
PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)`, DozerDB plan/db-hits, p50/p95/p99,
peak memory/CPU, timeout rate, result count, and projection freshness.

Reference hypothesis:
`https://memgraph.com/blog/postgresql-19-alternatives-memgraph`; reproduction
source cited by the article:
`https://github.com/memgraph/best-practices/tree/main/benchmarks/postgres_lpg_pokec_benchmark`.

### 3.4 GraphScope: deferred analytics plane

GraphScope is admitted only after a single DozerDB serving instance fails a
measured batch-analytics or scale gate. It is intended for offline large-graph
analytics, not the hot answer path. It must consume versioned snapshots or
watermarks so results can be related back to authoritative memory.

### 3.5 Mara and LiteLLM seam

Mara uses the OpenAI-compatible endpoint with `MiniMax-M2.7` as the primary
reasoning model. LiteLLM may provide routing, retries, budgets, and usage
accounting, but it is not an agent messaging bus. Agents exchange typed
intent, evidence, causal-token, and receipt references.

Raw model reasoning is never persisted. Record reasoning-token count, input
and output tokens, time to first token, latency, retries, and response schema
validity.

### 3.6 LiteLLM versus Envoy AI Gateway

Use LiteLLM first for the current SEOCHO evaluation because the immediate
requirements are model/provider normalization, structured-output behavior,
routing/fallback, per-model usage, budgets, and optional virtual keys. Do not
add Envoy AI Gateway as a second gateway in the default profile.

Admit Envoy AI Gateway only for a measured Kubernetes/networking profile that
needs Envoy's data plane, Gateway API integration, centralized mTLS/security
policy, high-throughput connection management, or infrastructure-owned rate
limiting. At that point compare it directly with LiteLLM on Mara traffic. A
possible later production layering is Envoy at the network edge and LiteLLM as
the model-aware control plane, but it is intentionally excluded until one
gateway fails a measured requirement.

LiteLLM and Envoy are gateways, not the agent-to-agent exchange protocol.
Agents continue to exchange typed intent, evidence, causal-token, and receipt
references through SEOCHO.

## 4. Canonical data model

The minimum durable entities are:

- `Workspace`, `User`, `Agent`
- `TransactionIntent`, `Transaction`, `TransactionEvent`
- `MemoryRevision`, `Supersedes`, `CausalToken`
- `ProjectionOutboxEntry`, `ProjectionWatermark`
- `Session`, `ContextEnvelope`, `MemoryUsageReceipt`
- `EvidenceBundle`, `AnswerReceipt`
- `OntologyVersion`, `PolicyVersion`, `PromptVersion`

Every durable row carries `workspace_id`, stable identity, revision or sequence,
event time, ingest time, provenance, and schema version. User-local sequence
ordering must not depend solely on wall-clock timestamps.

### 4.1 Context Envelope

Each answer request resolves a bounded envelope:

```json
{
  "workspace_id": "opaque",
  "session_id": "opaque",
  "required_causal_token": "...",
  "memory_revision_range": [1200, 1288],
  "graph_targets": ["user-hot", "transaction-history"],
  "projection_watermarks": {"user-hot": 1288},
  "ontology_version": "transaction-memory.v3",
  "policy_version": "disclosure.v2",
  "prompt_version": "answer-synthesis.v4",
  "intent_id": "transaction_history_explanation.v1",
  "required_slots": ["status", "counterparty", "timeline", "provenance"]
}
```

### 4.2 Prompt optimization and user visibility

The context composer emits a content-free `PromptAssemblyReceipt` showing:

- candidate, selected, and omitted sections
- bounded exclusion reasons
- estimated candidate/selected tokens and token budget
- compression and cacheable-prefix ratios
- evidence/provenance count and missing slots
- ontology, policy, and prompt identities

An authorized debug response exposes section IDs and reasons. OTel receives
only aggregate counts, versions, hashes, and ratios. Optimization succeeds only
when it reduces cost or latency without degrading correctness, provenance,
disclosure safety, or stale-memory detection.

## 5. Query execution policy

### 5.1 Approved recipes first

Known query families compile parameterized, workspace-scoped Cypher. Unknown
families enter Text2Cypher only after intent and schema selection. Generated
Cypher must be read-only, identifier-validated, bounded to four hops and a row
limit, checked with `EXPLAIN`, and allowed at most one repair.

### 5.2 Typed Graph-CoT

The reasoning lane is:

```text
QuestionFrame -> SupervisorDirective -> QueryEvidencePacket
              -> AnswerDraft -> GuardrailVerdict -> AnswerReceipt
```

The model may choose and explain a query plan, but may not invent facts, relax
workspace isolation, bypass disclosure, authorize a financial action, or hide
missing slots.

## 6. Evaluation dataset

### 6.1 Real-data component

Use current public Bitcoin blocks and transactions through Blockstream Esplora.
Persist raw addresses only in the isolated ingestion store when required;
reports use opaque keyed references. Freeze a manifest containing block hashes,
transaction IDs as controlled references, fetch time, and source version so a
run is reproducible.

### 6.2 Single-user longitudinal corpus

Bind public transaction shapes to one synthetic user and generate a governed
long history with:

- 100k baseline events, scalable to 1m and 10m load profiles
- repeated counterparties and recurring agent interactions
- cross-session preferences and prior decisions
- pending, confirmed, failed, replaced, and reversed transitions
- duplicate deliveries, out-of-order events, and reorg replacements
- ontology/policy/prompt version changes
- deliberately private fields used to test disclosure

The binding is synthetic and must not claim real address ownership.

### 6.3 Gold annotations

Gold labels describe deterministic system facts, not inferred wallet risk:

- expected transaction state and event ordering
- expected canonical memory revision
- required answer slots and supporting provenance
- expected missing slots or abstention
- allowed/denied fields by subject and role
- expected causal watermark behavior
- acceptable query family and hop bound

### 6.4 Agent-to-agent OKX transaction corpus

The core interaction corpus is separate from the on-chain longitudinal corpus.
It models `strategy_agent -> risk_agent -> execution_agent -> okx_demo ->
settlement_agent -> memory_agent -> support_agent` as a typed causal chain.
Each transaction records proposal, approval/rejection, place/amend/cancel,
acknowledgement, partial/full fill, settlement, and durable-memory publication.

The field vocabulary follows OKX v5 order/fill rows (`instId`, `instType`,
`clOrdId`, `ordId`, `state`, `side`, `posSide`, `ordType`, `sz`, `px`,
`accFillSz`, `avgPx`, `uTime`) but normalizes names and hashes exchange order
IDs. The default corpus is deterministic replay. An opt-in collector may use
`python-okx` with `flag="1"` for demo trading only; live trading (`flag="0"`)
is outside this evaluation and never enabled by default.

## 7. Evaluation queries and scenarios

The production-critical incident definitions, failure injections, and exact
pass/fail gates are specified in
[`OKX_CRITICAL_AGENT_MEMORY_SCENARIOS.md`](OKX_CRITICAL_AGENT_MEMORY_SCENARIOS.md).
The Q1-Q12 catalog below remains the broader coverage index.

### Q1. Cross-session memory recall

> 지난 세션에서 내가 반복 거래 대상으로 지정한 agent와 최근 세 건의
> 거래 상태를 근거와 함께 알려줘.

Validates durable user memory, session continuity, temporal ordering, selected
revision, provenance, and prompt compression.

### Q2. Point-in-time explanation

> sequence 82,400 시점에 agent B로의 전송이 pending으로 답변된 이유는
> 무엇이었고 지금 상태와 무엇이 달라졌나?

Validates time travel, supersession, historical answer reproducibility, and
current-versus-prior evidence separation.

### Q3. Multi-hop agent transaction path

> user -> settlement agent -> routing agent -> counterparty까지 이번 전송에
> 참여한 경로와 각 agent의 상태 전이를 보여줘.

Validates bounded graph retrieval, relationship provenance, and Graph-CoT.

### Q4. Federated database retrieval

> user-hot과 transaction-history DB를 함께 조회해 미완료 거래와 관련된
> 과거 상호작용을 합쳐 설명해줘.

Validates application-level fan-out, timeout isolation, deduplication, merged
evidence, partial-result disclosure, and per-target latency.

### Q5. Causal consistency under projection lag

> 방금 기록한 transaction intent를 포함해서 현재 상태를 알려줘.

Run immediately after PostgreSQL commit while DozerDB is behind. The system
must wait within budget, fall back to authoritative memory, or return explicit
staleness; it must not silently answer from an older graph.

### Q6. Concurrent agent exchange

Run multiple agents updating the same transaction intent while support agents
read it. Validate idempotency, fencing, serialization/conflict handling,
bounded retry, and monotonic answer revisions.

### Q7. Duplicate and out-of-order ingestion

Replay the same transaction batch and deliver an earlier state after a later
state. Validate no duplicate memory/outbox work and deterministic ordering.

### Q8. Blockchain reorganization and rollback

Replace a canonical block after an answer was produced. Validate orphan
revisions, compensating projection, historical receipt preservation, and a new
answer that cites the replacement revision.

### Q9. Ontology disclosure guardrail

Ask the same question as the user, support agent, and unrelated agent. Validate
that each receives only allowed properties and that denied data never reaches
Mara or telemetry.

### Q10. Text2Cypher safety and repair

Use a query not covered by an approved recipe. Validate schema grounding,
workspace predicate, hop/row bounds, `EXPLAIN`, one repair, and deterministic
refusal of writes or unrestricted traversal.

### Q11. Long-context prompt optimization

Ask a question with thousands of eligible memories but only a small causal
subset. Compare full-context, recency-only, and context-graph selection with
the same model and answer contract.

### Q12. Service degradation and recovery

Inject etcd unavailability, one DozerDB target timeout, Mara rate limits,
projector worker loss, and OTel exporter failure separately. Validate bounded
degradation, recovery, and explicit partial/stale status.

## 8. Metrics and acceptance gates

The normative production catalog, units, labels, enablement profiles,
dashboards, alerts, privacy rules, and verification gates live in
`docs/PRODUCTION_OBSERVABILITY_METRICS_SPEC.md`. This section summarizes the
evaluation gates; it does not replace the production contract.

| Dimension | Metrics | Initial gate |
|---|---|---|
| Memory correctness | idempotency, revision accuracy, rollback/reorg parity | 100% deterministic cases |
| Grounding | slot accuracy, provenance coverage, unsupported claims | >= 0.98, >= 0.99, 0 critical |
| Disclosure | forbidden field in prompt/answer/trace | 0 |
| Consistency | stale answer without explicit status, watermark violation | 0 |
| Retrieval | recall@k, target timeout, partial-result accuracy, db hits | baseline recorded; no unbounded query |
| Prompt optimization | input tokens, compression, cache hit, quality delta | >= 30% token reduction; quality delta >= -1 pp |
| LLM | schema validity, TTFT, p50/p95/p99, retries, cost/supported answer | report per model and concurrency |
| Concurrency | throughput, conflict/retry, error rate, queue lag | sweep 1/8/32/64; no lost commits |
| Projection | outbox depth/age, sequence/time lag, rebuild parity | no silent stale read; rebuild parity 100% |
| Observability | dropped spans, exporter errors, p95 overhead | tracing p95 overhead <= 3% |

Thresholds are initial hypotheses, not production SLAs. Every report must name
hardware, service versions, dataset manifest, concurrency, sampling, and warmup.
Mock and in-memory results are contract evidence only and must never be used as
latency, throughput, scalability, compatibility, or production-readiness
evidence. Each such claim requires a run against every service named by it.

## 9. OTel span and dashboard contract

Root spans:

- `agent.memory_answer`
- `memory.ingest`
- `agent.exchange`
- `projection.batch`

Key children:

- `context.resolve_descriptor`
- `memory.read_revisions`
- `graph.federated_retrieve` / `graph.query`
- `context.assemble`
- `guardrail.disclosure`
- `gen_ai.chat`
- `memory.usage_receipt`

Do not create spans per token, row, node, edge, or low-level etcd get. Metrics
are always on; normal production traces are sampled. Errors, slow requests,
rollback/reorg, stale watermark, disclosure failure, and evaluation runs are
retained. Collector export is asynchronous and must not fail the request.

Grafana dashboards:

1. Service Overview: rate, outcomes, p50/p95/p99, inflight, SLO burn
2. Memory Consistency: commits, outbox depth/age, watermark, replay, reorg
3. GraphRAG and Context: federation/Text2Cypher, evidence and token compression
4. LLM and Agent Exchange: TTFT, tokens, cost, retry/repair and handoff loops
5. Governance and Audit: disclosure, version drift, receipts and provenance
6. Dependencies: PostgreSQL, DozerDB, etcd and telemetry-pipeline saturation
7. Evaluation: versioned blockchain dataset quality and S1-S10 scorecards

The evaluation dashboard is separate from production paging but remains a
supported SEOCHO surface. Capability-gated cluster, replication, and TLS
signals report `unsupported` when absent; they never emit fake healthy zeroes.

## 10. Implementation TODO

### P0. Freeze contracts and dataset

- [x] Define the PostgreSQL v1 schema and dependency-free migration contract.
- [x] Define Context Envelope, Memory Usage Receipt, and Answer Receipt models.
- [x] Freeze the deterministic single-user dataset generator; the real-chain
  source manifest remains to be versioned by the public-chain fetch lane.
- [x] Add gold query/slot/provenance/disclosure fixtures for Q1-Q12.
- [ ] Record model, prompt, ontology, policy, and dataset versions in every run.

### P1. Authoritative memory and coordination

- [ ] Implement PostgreSQL memory repository with append-only revisions.
- [ ] Implement atomic idempotency, state transition, outbox, and usage receipt.
- [ ] Implement point-in-time and causal-token reads.
- [ ] Add reorg/rollback compensation and deterministic replay.
- [ ] Add etcd lease/fencing/watermark adapter with strict value validation.
- [ ] Retain in-memory repository for deterministic tests.
- [ ] Keep FoundationDB as an opt-in comparison adapter only.

Progress note: the first PostgreSQL repository slice now allocates a
workspace sequence under row lock, serializes writers per logical memory,
and commits revision, idempotency receipt, and projection outbox in one DB
transaction. Point-in-time/causal reads and transaction-state APIs remain.

### P2. Graph projection and retrieval

- [ ] Implement PostgreSQL-outbox to DozerDB projector and rebuild command.
- [ ] Define databases, constraints, indexes, and temporal/provenance properties.
- [ ] Enforce projection watermark on answer requests.
- [ ] Implement bounded application-level federation and partial-result contract.
- [ ] Add query-plan/db-hit capture and workload-specific index experiments.
- [ ] Keep GraphScope behind an offline scale benchmark gate.
- [ ] Reproduce exact N-hop reachability for PostgreSQL 19 SQL/PGQ versus
  DozerDB at hops 1-5 using only the Bitcoin transaction-memory graph.
- [ ] Publish result-parity, plan, latency, resource, timeout, and projection
  freshness results; do not reuse Memgraph's reported speedup as our claim.

### P3. Context, prompt, and Graph-CoT

- [ ] Integrate Prompt Assembly Receipt into the live context composer.
- [ ] Select memory by intent, required slots, causal range, and disclosure.
- [ ] Implement context compression and stable-prefix caching strategy.
- [ ] Surface compact optimization receipt in authorized API/debug responses.
- [ ] Connect MiniMax-M2.7 structured-output normalization.
- [ ] Run Graph-CoT versus deterministic recipe and direct-answer ablations.

### P4. Observability stack

- [x] Provision OTel Collector, Tempo, Prometheus, and Grafana locally.
- [ ] Instrument only the stable E2E and I/O boundaries in section 9.
- [x] Define the normative low-cardinality metric, label, profile, privacy,
  SLO, dashboard, and verification contract in
  `docs/PRODUCTION_OBSERVABILITY_METRICS_SPEC.md`.
- [ ] Implement the production metric instruments and Collector
  redaction/batch/memory limits from that contract.
- [ ] Configure sampling, exemplars, dashboards, and alert rules.
- [ ] Benchmark tracing off/on overhead at concurrency 1/16/32.
- [ ] Scrape Neo4j/DozerDB cluster metrics and add low-frequency TLS
  expiry/handshake/reload probes per
  [`NEO4J_TLS_OBSERVABILITY.md`](NEO4J_TLS_OBSERVABILITY.md).

### P5. Reliability and load evaluation

- [ ] Build ingestion sweeps for 100k/1m/10m events.
- [ ] Build query and agent-exchange sweeps for concurrency 1/8/32/64.
- [ ] Add failure injection for Q5-Q8 and Q12.
- [ ] Run dataset-fixed Mara E2E and prompt optimization A/B tests.
- [ ] Produce machine-readable scorecard and Grafana snapshots.
- [ ] Document every skipped live-service gate as a gap.

First live PostgreSQL result: `docs/experiments/okx-agent-transactions/
postgres-live-2026-07-11.json`. PostgreSQL 18.4 processed the same 635-event
agent corpus at concurrency 1/8/32 with no lost commits, exact revision/outbox
parity, and zero events reapplied during replay. Concurrency 8 had the highest
bounded throughput (221.68 events/s); concurrency 32 increased p95 to 248.107
ms without improving throughput. This is evidence for PostgreSQL only, not the
unstarted DozerDB/etcd/Mara/OTel lanes.

First live PostgreSQL-to-DozerDB result:
`docs/experiments/okx-agent-transactions/postgres-dozerdb-live-2026-07-11.json`.
All 635 pending entries projected in seven batches; PostgreSQL then reported
pending zero and watermark 635, while replay projected zero entries. DozerDB
contained the expected 409 typed nodes and 748 relationships. Exact directed
agent-handoff traversal p95 stayed between 7.193 and 10.935 ms for one-to-four
hops in this small graph. This is a bounded live measurement, not a sustained
load or PostgreSQL SQL/PGQ comparison.

First live Mara MiniMax-M2.7 result:
`docs/experiments/okx-agent-transactions/mara-minimax-m27-live-2026-07-11.json`.
Six disclosure-filtered cases at concurrency three completed 6/6 with exact
disposition and provenance, zero forbidden-field leakage, and 3,247.99 ms p95.
The live run found a model-specific evidence-echo array shape; normalization
now selects the unique object satisfying the complete output schema. Raw
reasoning and completions were not persisted.

First live OTel stack result:
`docs/experiments/okx-agent-transactions/otel-stack-live-2026-07-11.json`.
SEOCHO exported one nested trace through Collector 0.156.0 into Tempo 3.0.0;
Prometheus 3.13.1 reported the Collector target up and Grafana 13.1.0 loaded
both datasources. This validates transport and topology only. Trace overhead,
sampling, application metrics, TLS probes, and alerts remain live gates.

First live etcd/OTel-overhead/TLS-capability result:
`docs/experiments/okx-agent-transactions/
coordination-observability-live-2026-07-11.json`. etcd 3.5.17 stored only
validated policy/watermark pointers, bound projector ownership to a 30-second
lease, and removed the owner after expiry. A 200-tree OTLP transport probe
added about 0.205 ms per four-span tree including final flush amortization;
this is not an E2E p95 claim. The current DozerDB image exposed no matching
dynamic TLS reload or SSL policy settings, so TLS reload remains unverified.

### P6. Production enablement

- [ ] Provide one-command local demo and one-command evaluation run.
- [ ] Provide `seocho observability enable` profiles and one-command live
  verification.
- [ ] Publish architecture, trace waterfall, seven production/evaluation
  dashboards, alerts, and scorecard.
- [ ] Map results back to the five OKX responsibilities in section 2.
- [ ] Separate measured claims from design targets and future scale work.

## 11. Benefits expected from the complete evaluation

### Product benefit

Users receive answers that state which memory revision and graph evidence were
used, whether anything was missing or stale, and how context was reduced. This
makes long-term agent memory understandable rather than opaque.

### Engineering benefit

The authoritative store, coordination plane, serving graph, and LLM interface
have non-overlapping responsibilities. Each can be scaled or replaced without
changing the evidence and receipt contracts.

### Reliability benefit

Replay, reorg, duplicate delivery, projection lag, and partial federation are
normal tested states. Recovery no longer depends on manually repairing a graph
whose historical basis is unknown.

### Cost and performance benefit

Context selection, stable prefixes, bounded graph queries, and tiered query
planning reduce LLM and database work. The A/B gates prevent a cheaper prompt
from silently producing worse answers.

### Hiring-signal benefit

The demonstration connects research concepts—Context Graphs, GraphRAG,
Graph-CoT, long-term memory, ontology guardrails—to typed interfaces,
transaction semantics, load tests, failure recovery, and observable quality.
It therefore addresses the infrastructure role more directly than a demo that
only shows a successful LLM response.

## 12. Non-goals

- claiming wallet ownership or complete illicit-activity labels
- allowing an LLM to authorize or execute a financial transaction
- storing customer or transaction data in etcd
- assuming native cross-instance DozerDB federation
- putting GraphScope in the online request path before a measured need
- persisting raw prompts, completions, or model reasoning by default
- calling an in-memory or smoke benchmark production-ready
