# SEOCHO Production Observability Metrics Specification

Status: normative implementation contract  
Last updated: 2026-07-11

## 1. Purpose

SEOCHO observability is a supported product capability, not a demo harness.
Every enabled execution plane must expose enough telemetry to answer four
questions without inspecting prompts, wallet identifiers, or transaction
payloads:

1. Are users receiving correct, current, policy-compliant answers?
2. Is authoritative memory durable and is every serving projection converging?
3. Which dependency or processing stage consumes the latency and capacity?
4. Can an operator reproduce the answer and explain any degradation?

The baseline follows the four SRE signals: latency, traffic, errors, and
saturation. Domain signals for memory correctness, retrieval quality, context
efficiency, model behavior, and governance extend that baseline.

## 2. Enablement model

`seocho observability enable` SHOULD install the common application telemetry,
OTel Collector pipelines, recording rules, alert rules, and dashboards. A
deployment emits a metric only when its owning component is enabled and
observed. It MUST NOT synthesize healthy replication, TLS rotation, cluster, or
provider-usage signals for components that are absent.

| Profile | Enabled signals |
|---|---|
| `core` | agent E2E, memory, projection, retrieval, context, LLM, governance |
| `postgresql` | DB client plus server locks, transactions, WAL/checkpoints |
| `dozerdb` | graph query, Bolt pool, transaction, cache, checkpoint, cluster when available |
| `etcd` | proposals, leader, apply lag, leases, quota, fencing |
| `cluster` | PostgreSQL replication and graph Raft/replica lag |
| `tls` | expiry, handshake failure, reload result; only for a TLS-enabled backend |
| `evaluation` | fixed-dataset S1-S10 results and quality scorecards |

Missing optional profiles appear as `unsupported` in deployment inventory, not
as a zero-valued success metric.

## 3. Metric conventions

- OTel instrument names use dotted semantic names and UCUM units. Prometheus
  renders dots as underscores and appends `_total` to counters.
- Durations are histograms in seconds. Recommended initial buckets are
  `0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30`.
- One duration metric includes successful and failed operations; `error.type`
  distinguishes failures.
- Counters describe events. Observable gauges describe current state. Derived
  values such as projection lag and error ratio SHOULD be recording rules,
  rather than duplicate application instruments.
- Metrics are always on. Traces are sampled. Logs are structured and
  rate-limited.
- Exemplars SHOULD connect latency and error histogram samples to retained
  Tempo traces.

### 3.1 Allowed bounded attributes

The default label budget permits:

- `service.name`, `deployment.environment.name`, `service.version`;
- `operation`, `outcome`, `error.type`, `query.class`, `stage`;
- `db.system.name`, `db.namespace`, `db.operation.name`;
- `gen_ai.provider.name`, `gen_ai.request.model`;
- `projection`, `support_status`, `policy.disposition`;
- `scenario_id` only in the `evaluation` profile.

Enumerations MUST be documented and bounded. An instrument SHOULD stay below
100 active series per service by default and MUST undergo a cardinality review
before exceeding 1,000.

### 3.2 Forbidden attributes and content

The following MUST NOT be metric labels:

- `workspace_id`, user/session/conversation IDs;
- wallet, account, order, transaction, event, memory, or trace IDs;
- prompt, response, Cypher, SQL, retrieved text, or tool arguments;
- hashes created from any of the identifiers above if they remain unbounded.

Raw query text is opt-in trace content, never a metric. Use a bounded query
summary or template name. Prompt and completion capture remains off by default.

## 4. Production metric catalog

`Required` means every core deployment emits the signal. `Conditional` means
the named feature owns it. `Evaluation` never pages production operators.

### 4.1 User-facing agent service

| Instrument | Type | Required attributes | Level |
|---|---|---|---|
| `seocho.agent.request.duration` | histogram `s` | `operation`, `outcome`, optional `error.type` | Required |
| `seocho.agent.request.count` | counter | `operation`, `outcome` | Required |
| `seocho.agent.request.inflight` | up/down counter | `operation` | Required |
| `seocho.agent.timeout.count` | counter | `operation`, `stage` | Required |
| `seocho.agent.partial.count` | counter | `operation`, `reason` | Required |
| `seocho.answer.freshness_violation.count` | counter | `query.class` | Required |
| `seocho.answer.provenance.coverage` | histogram `1` | `query.class` | Required |
| `seocho.answer.required_slot.missing.count` | counter | `query.class`, `slot` | Required; slots are a bounded enum |

Success and error duration distributions must both remain queryable. The main
SLO is evaluated at the answer boundary, not inferred from dependency uptime.

### 4.2 Authoritative long-term memory

| Instrument | Type | Required attributes | Level |
|---|---|---|---|
| `seocho.memory.commit.duration` | histogram `s` | `outcome`, optional `error.type` | Required |
| `seocho.memory.commit.count` | counter | `outcome` | Required |
| `seocho.memory.sequence` | observable gauge | none | Required; aggregate safely across tenants |
| `seocho.memory.idempotency_replay.count` | counter | `outcome` | Required |
| `seocho.memory.transition_conflict.count` | counter | `event.type` | Required |
| `seocho.memory.point_in_time_read.duration` | histogram `s` | `outcome` | Required |
| `seocho.memory.rollback.count` | counter | `reason` | Conditional: rollback/reorg |
| `seocho.memory.reorg.depth` | histogram `{block}` | `network` | Conditional: blockchain |
| `seocho.memory.orphaned_event.count` | counter | `network` | Conditional: blockchain |
| `seocho.memory.replay.duration` | histogram `s` | `outcome` | Conditional: rebuild/recovery |

No metric exports a memory ID. Revision and sequence details required for one
answer live in its sampled trace and auditable receipt.

### 4.3 Projection and serving freshness

| Instrument | Type | Required attributes | Level |
|---|---|---|---|
| `seocho.projection.watermark` | observable gauge | `projection` | Required |
| `seocho.projection.outbox.pending` | observable gauge | `projection` | Required |
| `seocho.projection.outbox.oldest_age` | observable gauge `s` | `projection` | Required |
| `seocho.projection.batch.duration` | histogram `s` | `projection`, `outcome` | Required |
| `seocho.projection.batch.entry_count` | histogram `{entry}` | `projection` | Required |
| `seocho.projection.replay.count` | counter | `projection`, `outcome` | Required |
| `seocho.projection.fencing_rejection.count` | counter | `projection` | Required with distributed projector |
| `seocho.projection.worker.active` | observable gauge `{worker}` | `projection` | Required with distributed projector |

Recording rules compute:

```promql
seocho:projection_lag_events =
  clamp_min(seocho_memory_sequence - seocho_projection_watermark, 0)

seocho:projection_stalled =
  (seocho_projection_outbox_pending > 0)
  and (seocho_projection_outbox_oldest_age_seconds > 60)
```

Event lag and wall-clock oldest age are both required: event lag alone hides a
low-volume projector that has been stalled for hours.

### 4.4 Retrieval, federation, and Text2Cypher

| Instrument | Type | Required attributes | Level |
|---|---|---|---|
| `seocho.retrieval.duration` | histogram `s` | `source`, `outcome` | Required |
| `seocho.retrieval.candidate_count` | histogram `{item}` | `source` | Required |
| `seocho.retrieval.selected_count` | histogram `{item}` | `source` | Required |
| `seocho.federation.target.duration` | histogram `s` | `target`, `outcome` | Conditional: federation |
| `seocho.federation.partial.count` | counter | `reason` | Conditional: federation |
| `seocho.text2cypher.duration` | histogram `s` | `stage`, `outcome` | Conditional: Text2Cypher |
| `seocho.text2cypher.validation_failure.count` | counter | `reason` | Conditional: Text2Cypher |
| `seocho.text2cypher.execution_failure.count` | counter | `error.type` | Conditional: Text2Cypher |
| `seocho.text2cypher.plan_fingerprint_change.count` | counter | `query.class` | Conditional: plan regression |

`source`, `target`, and `query.class` are configuration enums. Database names
may be used; user-provided database or graph names may not.

### 4.5 Context management and prompt optimization

| Instrument | Type | Required attributes | Level |
|---|---|---|---|
| `seocho.context.assembly.duration` | histogram `s` | `strategy`, `outcome` | Required |
| `seocho.context.candidate_token_count` | histogram `{token}` | `strategy` | Required |
| `seocho.context.selected_token_count` | histogram `{token}` | `strategy` | Required |
| `seocho.context.item_count` | histogram `{item}` | `strategy`, `state=candidate|selected` | Required |
| `seocho.context.budget_exceeded.count` | counter | `strategy` | Required |
| `seocho.context.cache.request.count` | counter | `result=hit|miss` | Conditional: cache |
| `seocho.context.policy_filtered.count` | counter | `reason` | Required |

Token compression is a recording rule:

```promql
seocho:context_compression_ratio =
  1 - (
    sum(rate(seocho_context_selected_token_count_sum[5m])) /
    clamp_min(sum(rate(seocho_context_candidate_token_count_sum[5m])), 1)
  )
```

Compression is never an SLO by itself. A fixed blockchain evaluation dataset
must show that quality stays within the declared tolerance.

### 4.6 LLM and agent exchange

Use current OpenTelemetry GenAI semantic conventions where the provider exposes
the relevant fields.

| Instrument | Type | Required attributes | Level |
|---|---|---|---|
| `gen_ai.client.operation.duration` | histogram `s` | provider, model, operation, optional `error.type` | Required |
| `gen_ai.client.token.usage` | histogram `{token}` | provider, model, token type | Required when provider reports usage |
| `seocho.gen_ai.time_to_first_token` | histogram `s` | provider, model | Conditional: streaming |
| `seocho.gen_ai.retry.count` | counter | provider, model, reason | Required |
| `seocho.gen_ai.fallback.count` | counter | provider, from/to model | Conditional: routing |
| `seocho.gen_ai.structured_output_repair.count` | counter | provider, model, reason | Required for structured answers |
| `seocho.gen_ai.request_cost` | histogram `USD` | provider, model | Conditional: authoritative pricing configured |
| `seocho.agent_exchange.handoff.duration` | histogram `s` | from role, to role, outcome | Required for multi-agent runtime |
| `seocho.agent_exchange.loop_limit.count` | counter | workflow | Required for multi-agent runtime |

Cost is not emitted from guessed pricing. Usage or cost unavailable from Mara
is represented in deployment capability inventory and evaluation artifacts.
Agent role and workflow values are configured enums, never dynamic names.

### 4.7 Governance and auditability

| Instrument | Type | Required attributes | Level |
|---|---|---|---|
| `seocho.governance.disclosure_violation.count` | counter | `stage`, `policy.disposition` | Required |
| `seocho.governance.policy_decision.count` | counter | policy version, disposition | Required |
| `seocho.governance.policy_version_mismatch.count` | counter | `stage` | Required |
| `seocho.governance.ontology_version_mismatch.count` | counter | `stage` | Required |
| `seocho.governance.receipt_write.count` | counter | `receipt.type`, `outcome` | Required |
| `seocho.observability.trace_complete.count` | counter | `workflow`, `outcome` | Required for retained/evaluation traces |
| `seocho.observability.export_failure.count` | counter | `signal`, `exporter` | Required |
| `seocho.observability.dropped.count` | counter | `signal`, `reason` | Required |

Policy and ontology versions must be bounded release identifiers. Unknown or
user-provided strings are normalized to `other` before metric emission.

## 5. Dependency metrics

### 5.1 PostgreSQL

Application instrumentation emits stable OTel `db.client.operation.duration`
and connection-pool signals. A PostgreSQL exporter owns server metrics:

- connections active/idle versus limit and pool wait/timeouts;
- transaction commit/rollback, deadlocks, lock waiters and oldest transaction;
- tuples read/written and temporary bytes when diagnosing query pressure;
- checkpoint duration/frequency and WAL bytes;
- replication write/flush/replay lag only in a replication deployment;
- database size and disk headroom.

Do not export SQL text. Use `db.query.summary` from a reviewed bounded set.

### 5.2 DozerDB / Neo4j-compatible graph serving

When exposed by the deployed edition/profile, collect:

- query execution success/failure/latency;
- active/peak transactions, rollback, deadlock and termination;
- Bolt connection/thread pool saturation;
- page-cache hit/fault ratio, heap/GC pause and file-descriptor headroom;
- checkpoint/log rotation duration;
- Cypher replan events and plan-cache pressure;
- node/relationship counts as capacity trends, not health;
- last committed transaction and Raft/apply lag per member in cluster mode.

Unsupported Enterprise-only metrics remain capability gaps. SEOCHO must not
claim DozerDB TLS reload or cluster visibility until verified against the exact
image and edition.

### 5.3 etcd

Scrape the native etcd endpoint for:

- leader present and leader changes;
- proposal pending, failed, committed and applied;
- proposal commit/apply duration;
- backend commit/rebalance duration and DB size versus quota;
- peer round-trip and receive/send failure in multi-member mode;
- lease grant/renew/expiry failure at the SEOCHO adapter;
- projector fencing rejection at the SEOCHO adapter.

Key names and values are never telemetry labels.

### 5.4 Collector, Tempo, Prometheus, and Grafana

Monitor Collector accepted/refused/dropped spans and metrics, exporter queue
size/capacity, send failures, retry delay, process memory and CPU. Monitor
Prometheus target health and rule evaluation failures, Tempo ingestion/search
errors and storage pressure, and Grafana datasource/query errors. Observability
failure must not fail an answer, but sustained signal loss must alert.

## 6. Evaluation-only quality metrics

The `evaluation` profile uses a versioned blockchain dataset manifest and fixed
queries. It records, but does not page on:

- Recall@K, MRR or nDCG for graph retrieval;
- answer slot accuracy and temporal answer accuracy;
- provenance correctness and unsupported claim count;
- point-in-time reproduction and rollback/reorg parity;
- stale-answer detection rate;
- context compression versus answer-quality delta;
- structured-output validity and repair success;
- S1-S10 common gates and skipped live services.

Every result includes dataset/model/prompt/ontology/policy versions, service
versions, concurrency, warmup, hardware/container limits, and trace IDs.
Mocks may validate contracts but never produce production or performance
claims.

## 7. SLO and alert policy

Thresholds begin as measured workload hypotheses and graduate to SLOs only
after a representative baseline window. Alerts use multi-window burn rates
rather than one noisy percentile whenever an error-budget SLO exists.

### Page candidates

- user-visible error or timeout SLO burn;
- silent stale or disclosure violation greater than zero;
- authoritative commit loss/corruption signal;
- outbox oldest age over the freshness objective while pending is nonzero;
- all projectors absent or fencing failures sustained;
- etcd has no leader in multi-member production;
- trace/metric export unavailable long enough to breach audit requirements.

### Ticket or investigation candidates

- latency p95/p99 regression without SLO burn;
- connection pool wait, DB rollback/deadlock, or Cypher replan trend;
- LLM repair/retry/fallback or token/cost regression;
- context budget exceeded or cache effectiveness regression;
- TLS certificate expiry within the operational rotation window.

Evaluation score changes create regression tickets, not runtime pages.

## 8. Trace contract and sampling

Stable root spans are `agent.memory_answer`, `memory.ingest`,
`agent.exchange`, and `projection.batch`. Stable children cover memory read,
federated retrieval, context assembly, disclosure, model invocation, and
receipt write. Do not create spans per row, token, graph node/edge, or etcd get.

Sampling policy:

- retain all errors, timeouts, partial/stale results, rollback/reorg,
  disclosure failures, evaluation runs, and requests above the slow threshold;
- probabilistically sample ordinary successful requests;
- use parent-based sampling and tail rules at the Collector where supported;
- keep content capture off independently of sampling;
- benchmark tracing off/on at target concurrency and enforce the overhead gate.

## 9. Dashboard ownership

SEOCHO provisions seven dashboards:

1. **Service Overview** — rate, outcome, p50/p95/p99, inflight, SLO burn.
2. **Memory Consistency** — commits, conflicts, outbox depth/age, watermark,
   replay, rollback/reorg and stale fallback.
3. **GraphRAG and Context** — retrieval/federation/Text2Cypher latency and
   errors, candidates, selected evidence, token compression and budget.
4. **LLM and Agent Exchange** — duration, TTFT, usage, cost, retry/repair,
   fallback and handoff loops.
5. **Governance and Audit** — disclosure, version drift, receipts, provenance
   and trace completeness.
6. **Dependencies** — PostgreSQL, DozerDB, etcd and telemetry-pipeline health.
7. **Evaluation** — blockchain dataset quality and S1-S10 results.

The evaluation dashboard is deliberately separate from production paging, but
is still a supported SEOCHO surface.

## 10. Implementation and verification gates

- Unit tests validate instrument names, units, bounded labels, and forbidden
  content rejection.
- Integration tests send actual OTLP metrics and traces through the Collector
  and query them back from Prometheus and Tempo.
- Live tests create commit/project/query/model traffic at concurrency
  1/8/32/64 and verify that counters, histograms, watermarks, and exemplars
  match source-of-truth receipts.
- Fault tests stop or delay each dependency and verify the expected error,
  saturation, partial-result, and recovery signals.
- Cardinality tests count active series under a multi-user workload and fail
  when an instrument exceeds its declared budget.
- Privacy tests scan metric labels and trace attributes for forbidden IDs and
  content.
- Dashboard tests validate PromQL, datasource UIDs, panel presence, and live
  non-empty results after workload execution.
- Alert tests inject synthetic series into `promtool test rules` fixtures.

## 11. Primary references

- [Google SRE: Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/)
- [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/)
- [OpenTelemetry database client metrics](https://opentelemetry.io/docs/specs/semconv/db/database-metrics/)
- [OpenTelemetry GenAI observability](https://opentelemetry.io/blog/2026/genai-observability/)
- [PostgreSQL monitoring](https://www.postgresql.org/docs/current/monitoring.html)
- [Neo4j metrics reference](https://neo4j.com/docs/operations-manual/current/monitoring/metrics/reference/)
- [Neo4j cluster status endpoints](https://neo4j.com/docs/operations-manual/current/clustering/monitoring/endpoints/)
- [etcd metrics](https://etcd.io/docs/v3.6/metrics/)

