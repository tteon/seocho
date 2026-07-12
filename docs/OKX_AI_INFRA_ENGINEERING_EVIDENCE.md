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

### E-009 — Strict public-chain-to-answer release gate

`tags: [live-api, bitcoin-mainnet, ofac, long-term-memory, query-compiler, guardrail, mara, model-fallback]`

- Source: live OFAC SDN XML (518 XBT labels) and Blockstream Bitcoin mainnet;
  the bounded run fetched two confirmed transactions and derived 102 events
  across two blocks. Reports retain only an opaque wallet hash.
- Memory/query result: 102 events and outbox entries, two replayed blocks were
  idempotent no-ops, projection was current, and six queries compiled through
  the approved bounded recipe. No raw address entered the LLM cases.
- Primary model finding: MiniMax-M2.7 at concurrency 3 produced valid answers
  for 3/6 cases and repeated `StructuredOutputError` for the same three cases
  after one retry. The new strict gate correctly failed instead of returning a
  misleading successful process status.
- Fallback result: MARA `gpt-oss-120b` passed 6/6 without retry; disposition
  accuracy and provenance coverage were 100%, disclosure leakage was zero, and
  LLM p95 was 4,183.5 ms.
- Engineering benefit: provider/model portability is now an availability
  mechanism with an explicit quality gate, not an assumption that every
  OpenAI-compatible model implements structured output identically.
- Passing artifact SHA-256: `179368b3d5b6c46bc1ad2f3757791311ee6d16870e296d30cc834fc3e40c2dca`.
- Failed-primary artifact SHA-256: `a971768cd6b03e127fd241a7cbe82934922fc363b7fa1d8ec91e7c25296fccc3`.
- Limitation: this public labelled seed has only two transactions; sustained
  scale is established separately by synthetic blockchain-shaped workloads
  and must not be presented as public-chain volume.
- Non-blocking TODO: characterize MARA MiniMax-M2.7 structured-output
  compatibility separately. It does not block SEOCHO while the strict gate and
  a validated provider/model fallback remain enabled.

### E-010 — One-command live release verdict

`tags: [live, release-gate, bitcoin-mainnet, mara, postgresql, etcd, dozerdb, prometheus, tempo, grafana]`

- Command surface: `make okx-release-gate`, with PostgreSQL DSN and graph
  password supplied through environment variables rather than committed files.
- Verdict: all five gates passed in the same run: public-chain memory,
  query/guardrail, answer generation, distributed failover, and observability
  backend readiness.
- Public lane: 102 events from two current Bitcoin transactions; 6/6 MARA
  `gpt-oss-120b` answers succeeded, accuracy and provenance coverage were 100%,
  leakage was zero, retry count was zero, and LLM p95 was 3,189.5 ms.
- Distributed lane: 300/300 PostgreSQL revisions were acknowledged and present
  in DozerDB, pending outbox was zero, stale token 33 was rejected, and token 35
  took ownership in 3,057.4 ms after the active process was terminated.
- Observability lane: Prometheus, Tempo, and Grafana readiness endpoints were
  healthy. This is backend availability; retained trace-content evidence
  remains the separate E-008 assertion.
- Verdict artifact SHA-256: `83c4102b491af1e24a95c7414655c477e1be6c686ed56a4d9917a4a7d66f0c2c`.
- Public-lane artifact SHA-256: `e309596e6958b2af26ae0563767f98372abe4b729b6fd167c8c271936670d3ba`.
- Failover-lane artifact SHA-256: `0f0e7254befded7fa99580979ae1da546899f52c53a21e24089f1b673d736e6e`.

### E-011 — User-question to memory-answer utility

`tags: [live, intent-routing, approved-recipe, text2cypher, postgresql, dozerdb, mara, context-optimization]`

- Dataset: 100 exchange-shaped intents generated 866 deliveries across 15
  lifecycle/failure scenarios. PostgreSQL retained 863 unique revisions and
  idempotently ignored three duplicate deliveries. Scenario frequencies are
  synthetic hypotheses, not exchange production statistics.
- Path: the evaluator receives only the user question, classifies one of six
  supported transaction-memory intents, compiles a workspace-scoped approved
  recipe, retrieves the DozerDB causal event chain, builds a cache-stable policy
  prefix plus variable evidence suffix, and asks MARA `gpt-oss-120b` to answer.
- Result: 6/6 intent, evidence-contract, state/support-status, and disclosure
  gates passed. Projection lag was deliberately injected and detected; no
  restricted field leaked. LLM p95 was 5,817.7 ms.
- Context A/B: full context used 60 events (~2,994 estimated tokens), while
  causal selection used 10 events (~499 tokens). Both returned the correct
  state, an estimated 83.33% input reduction. Token counts are byte-based
  estimates because MARA does not return provider token accounting.
- Unknown intent: MARA Text2Cypher generated a workspace-scoped, parameterized
  read query. Label, relationship, property, hop, result-limit, and tenant-scope
  validation plus live DozerDB `EXPLAIN` preceded execution; it returned 10
  evidence rows. A prior run was correctly discarded after exposing validator
  gaps for an unknown property and unbounded `*0..` path.
- Utility artifact SHA-256: `3fad58ea37cc63ffcc186adddb42c49a9da2bbed893a5e1e8217bb8f24c44fca`.
- Text2Cypher artifact SHA-256: `fd4b4f434716d4669c15482c039e71a4498650ee1bc5783b20a5f797b5faa60f`.

#### Q1–Q12 coverage audit

| Query | Capability | Evidence level |
|---|---|---|
| Q1 | cross-session/current memory | live current state; cross-session gold contract |
| Q2 | point-in-time/supersession | live event chain; explicit historical-answer scorer pending |
| Q3 | bounded agent handoff | live answer path |
| Q4 | federated history | contract only; live multi-target answer pending |
| Q5 | causal read/projection lag | live answer path |
| Q6 | concurrent canonical state | live DB/fencing plus answer path |
| Q7 | duplicate/out-of-order ingest | live duplicate; deterministic out-of-order |
| Q8 | reorg/rollback | deterministic contract; PITR live gate pending |
| Q9 | ontology disclosure | live disclosure gate |
| Q10 | bounded Text2Cypher | live generation, validation, EXPLAIN, execution |
| Q11 | long-context selection | live A/B |
| Q12 | dependency degradation | live projection-lag partial answer; broader outage pending |

This matrix prevents a gold-query declaration from being presented as a live
capability. Q4 and Q8 remain the principal utility gaps.

### E-012 — Real-world incident scenario rerun

`tags: [live, incident, causal-read, projector-replay, scenario-scorecard]`

- Fresh S1 result: ten transaction lifecycles produced 65 memory revisions;
  the deliberately stale graph caused one authoritative PostgreSQL fallback,
  silent stale answers were zero, and projection caught up to watermark 65.
- Fresh S4 result: five already-applied outbox entries were replayed after an
  injected acknowledgement loss. Graph cardinality remained 49 nodes and 88
  relationships before and after, pending outbox returned to zero, and the
  watermark remained monotonic.
- Artifact SHA-256: `61419ae171da7b60b98b769b6749be0e6ca2917f623d3bc423e6cf63e788e98f`.

| Scenario | Current evidence | Verdict |
|---|---|---|
| S1 stale read after commit | fresh live PostgreSQL/DozerDB/etcd/Tempo fault injection | pass |
| S2 conflicting agent decisions | live cancel/fill lifecycle; same-intent concurrent-writer injection pending | partial |
| S3 disputed historical fill | live ordered revision chain; historical answer isolation pending | partial |
| S4 crash before acknowledgement | fresh live idempotent graph replay | pass |
| S5 long-horizon context | live 60-vs-10 event A/B; million-event quality gate pending | partial/pass at tested scale |
| S6 partial federation | application contract only; physical target timeout run pending | not executed |
| S7 policy/ontology drift | calibrated policy-drift data only | not executed |
| S8 chain reorganization | deterministic memory contract only; live PostgreSQL projection replay pending | not executed live |
| S9 model degradation | live MiniMax failure and strict gpt-oss fallback | pass at tested cases |
| S10 TLS rotation | unsupported by current DozerDB image | blocked/capability-gated |

Only rows marked `pass` are suitable as live CV claims. Partial, unexecuted,
and capability-gated rows remain engineering work, not inferred evidence.

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
