---
title: SEOCHO AI Infrastructure Engineering Evidence
status: active
domain: blockchain-agent-transactions
target_role: OKX AI Infrastructure Engineer
system: seocho
evidence_policy: live-results-first
last_verified: 2026-07-12
tags:
  - long-term-memory
  - graphrag
  - context-graph
  - prompt-optimization
  - distributed-systems
  - postgresql
  - etcd
  - chaos-engineering
  - dozerdb
  - arrow
  - parquet
  - opentelemetry
  - governance
---

# SEOCHO AI Infrastructure Engineering Evidence

This is the canonical, cumulative evidence document. Detailed ADRs define
individual decisions, but measured engineering claims must be summarized here
with their provenance, limitation, and reproducible artifact.

## Product boundary

| Plane | Component | Owns | Must not own |
|---|---|---|---|
| authoritative data | PostgreSQL | memory revisions, idempotency, outbox, watermarks, fencing, receipts | graph traversal serving |
| coordination | etcd | leases, owner, fencing token, active policy/ontology pointer, watermark pointer | users, wallets, transactions, prompts, evidence bodies |
| projection serving | DozerDB | workspace-scoped graph projection, indexes, bounded traversal | authoritative memory |
| projection transport | Arrow / Parquet | batch transfer; immutable replay/audit artifact | lease ownership |
| model interface | MARA/Kimi/on-prem adapters | reasoning and answer generation | deterministic authorization or disclosure control |
| observability | OTel/Tempo/Prometheus/Grafana | hashes, versions, counts, durations, outcomes | raw customer content by default |

## OKX requirement mapping

| Requirement | SEOCHO implementation | Evidence state |
|---|---|---|
| auditable, versioned, rollback-capable long-term memory | PostgreSQL revisions, causal sequence, idempotency, outbox, Parquet receipt, replay parity | measured |
| end-to-end GraphRAG | PostgreSQL → Arrow/Parquet → DozerDB → bounded retrieval → MARA | measured |
| scalable context management | ontology/context graph, Prompt Package, cache-stable prefix, request evidence suffix | measured |
| research to production | query-first hops, GOpt-style plan audit, Graph-CoT seam, APOC transport A/B/C | measured/in progress |
| reliability, observability, governance | etcd leases/fencing, disclosure guardrail, OTel metrics/traces, critical gates | measured |

## Evidence registry

### E-001 — Long-term memory correctness

`tags: [live, postgresql, long-term-memory, replay, audit]`

- Workload: blockchain/exchange agent memory.
- Result: 130 events projected with zero loss; replay preserved graph
  cardinality (88 nodes / 161 relationships).
- Benefit: the graph can be rebuilt without becoming the source of truth.
- Limitation: larger recovery-time curves remain a continuing capacity test.

### E-002 — Concurrent retrieval

`tags: [live, dozerdb, concurrency, graphrag, retrieval]`

- Workload: 16,000 graph queries.
- Result: zero errors; concurrency 8 = 1,658 QPS, concurrency 64 = 1,622 QPS.
- Benefit: bounded retrieval remains stable under the tested concurrency range.
- Limitation: one local node is not a distributed-cluster claim.

### E-003 — Query-first reasoning

`tags: [live, query-first, graph-cot, context-graph, governance]`

- Result: 5/5 scenarios passed; actual evidence required two 1-hop and three
  2-hop traversals. No artificial 5-hop requirement.
- Benefit: traversal depth follows the user question and evidence contract.
- Limitation: expand scenario diversity rather than forcing deeper paths.

### E-004 — MARA answer generation

`tags: [live-api, mara, minimax-m2.7, answer-generation, leakage]`

- Result: 20/20 successful; state, freshness, and action accuracy 100%; leakage
  cases 0; retrieval p95 33.2 ms; LLM p95 2.20 s.
- Benefit: authoritative state and graph evidence survive model synthesis.
- Limitation: MARA does not report cached input tokens.
- Artifact: `/tmp/seocho-agent-memory-experiments/docs/mara-e2e-after-prompt-cache-2026-07-12.json`.

### E-005 — Prompt caching and format

`tags: [live-api, kimi, prompt-package, prefix-cache, cost]`

- Workload: Kimi K2.5/K2.6/K2.7, 15 cold/warm pairs.
- Result: warm cache hit 15/15; K2.5 and K2.7 cached 3,328 of ~3,440 input
  tokens; K2.6 cached 2,048 of ~3,441.
- Benefit: under the measured 80% cached-token discount, estimated total input
  reduction was 77.4%, 47.6%, and 77.4% respectively.
- Limitation: Kimi latency was unsuitable for the primary E2E, so MARA remains
  the active reasoning backend while cache semantics remain portable.
- Artifact SHA-256: `ead4e5932f7a6c01a6e8acb76cb8e0a570be38289206041526ebf83c2de64be7`.

### E-006 — Projection transport A/B/C

`tags: [live, postgresql, dozerdb, apoc-extended, arrow, parquet, performance]`

- Data: first 20,000 actual revisions from an 84,944-revision PostgreSQL
  workspace; sequence 1–20,000; row parity 100%.
- Median graph-write latency: Bolt UNWIND 550.5 ms; APOC Arrow 186.7 ms;
  APOC Parquet 144.7 ms.
- Benefit: Arrow was 2.95× and Parquet 3.80× faster than Bolt in this bulk
  workload. Parquet was 2.74 MB versus canonical JSON 26.51 MB.
- Engineering finding: Arrow stream and file framing are distinct; SEOCHO now
  exposes separate encoders to prevent protocol confusion.
- Limitation: APOC reports `apoc.load.arrow` deprecation/migration messaging;
  keep a capability-gated Bolt fallback and monitor the Extended API.
- Artifact: `/tmp/seocho-agent-memory-experiments/docs/projection-transport-live-20k-2026-07-12.json`.
- Artifact SHA-256: `f4a5019dd888af53c8a0751ec6572b5565d9d6c2c2abfcdf44e916afc8c43d2d`.

### E-007 — Distributed projector failover and fencing

`tags: [live, etcd, postgresql, dozerdb, lease, fencing, process-kill, split-brain]`

- Workload: 300 revisions continuously committed to live PostgreSQL while an
  actual projector process held the etcd owner lease (2,746.7 ms ingestion).
- Fault: projector A was terminated with SIGTERM (`exitcode=-15`). Projector B
  was blocked while A was alive, then acquired ownership 3,299.7 ms after the
  kill under a three-second lease TTL. Its token advanced from 29 to 31.
- Result: 300 revisions = 300 acknowledged outbox records = 300 live DozerDB
  nodes; pending outbox 0; durable watermark 300/token 31. Stale token 29 was
  rejected before graph mutation.
- Write ordering: the repository checks the durable fence before graph mutation
  and checks it again atomically while acknowledging outbox rows and watermark.
  A known-stale worker therefore cannot pollute the graph before being rejected.
- Benefit: etcd provides fast ephemeral ownership, while PostgreSQL prevents a
  paused/stale worker from acknowledging a graph write after lease loss.
- Governance: current etcd data contains only active policy and watermark
  pointers; no customer or transaction payloads.
- Artifact: `/tmp/seocho-agent-memory-experiments/docs/projector-failover-chaos-live-2026-07-12.json`.
- Artifact SHA-256: `a8a9e976fef33c427e6b5d10faaaf4d59010343edb73b37717bd37b9e768a573`.
- Limitation: this proves the causal path on one etcd member and one DozerDB
  instance; repeated kills and multi-member quorum behavior remain capacity
  and availability gates.

### E-008 — Observability

`tags: [live, opentelemetry, tempo, prometheus, grafana, privacy]`

- Result: live `db.query` and `gen_ai.chat` traces were retrieved from Tempo.
- Dashboard: critical gates, projection lag, cache-hit ratio, cached-input
  ratio, and estimated input-cost reduction.
- Privacy: prompt/evidence bodies are disabled by default; telemetry carries
  bounded hashes, versions, counts, and outcomes.

## Current engineering decisions

1. PostgreSQL remains authoritative; DozerDB is disposable and replayable.
2. etcd remains a small control plane, never a customer-memory database.
3. Arrow is the live batch contract; Parquet is the compact recovery/audit
   contract. APOC acceleration is capability-gated.
4. Prompt security is deterministic and pre-LLM; prompt optimization cannot
   replace ontology disclosure enforcement.
5. Every performance claim requires live provenance, parity, and an explicit
   limitation. Synthetic workloads supplement but never replace real data.

## Next gates

- repeated process-kill/failover cycles and recovery-time distribution;
- point-in-time Parquet rollback followed by graph/cardinality/query parity;
- Arrow/Parquet performance across batch-size and concurrency sweeps;
- long-session context selection with superseded-memory exclusion;
- query plan regression gate with semantic answer parity;
- multi-node etcd and graph editions only when the single-node causal path is
  fully measured.
