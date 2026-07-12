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

| JD requirement | SEOCHO implementation | Live acceptance evidence | Current verdict |
|---|---|---|---|
| auditable, versioned, rollback-capable long-term memory | PostgreSQL revisions, causal sequence, idempotency, outbox, Parquet receipt, replay parity | S2 writer conflict, S3 historical snapshot isolation, S8 append-only reorg compensation and idempotent graph rebuild | passed |
| end-to-end GraphRAG | PostgreSQL → Arrow/Parquet → DozerDB → bounded retrieval → validated Text2Cypher → MARA | 20K Arrow/Parquet projection parity; live Text2Cypher `EXPLAIN`, one attempt, ten rows; MARA evidence contract | passed |
| scalable context management | ontology/context graph, Prompt Package, cache-stable prefix, request evidence suffix | 84,944-revision source workspace; 83.33% context-token reduction with answer parity; 10K English intent/source-routing cohort | passed at tested scale |
| research to production | query-first hops, GOpt-style plan audit, Graph-CoT seam, provider fallback, APOC transport A/B/C | measured baseline/candidate plans, protocol capability gates, strict structured-output failure and fallback | passed; continuous optimization remains operational work |
| reliability, observability, governance | etcd leases/fencing, partial federation, disclosure guardrail, OTel metrics/traces, SLO burn alerts | S6 target loss, S7 policy pointer change, process-kill fencing, live 17-span E2E waterfall, zero leakage; TLS reload is explicitly capability-gated without Enterprise | passed except environment-gated S10 |

The acceptance rule is evidence-first: a unit test proves a contract, a live
artifact proves the tested runtime, and neither is promoted to a distributed or
Enterprise claim beyond the environment that produced it.

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

- Result: Grafana Evaluation dashboard version 7 exposes eighteen panels,
  including a Tempo table that opens a stage waterfall in the same interface.
  Live trace `5e6ee42972187b01a09c8eba575ea3fd` contains seventeen spans under
  `okx.e2e.run`: PostgreSQL concurrency/history, federation/etcd governance,
  reorg compensation/rebuild, validated Text2Cypher, MARA answer generation,
  one Text2Cypher `gen_ai.chat`, and ten answer-contract `gen_ai.chat` spans.
- Measured stage time: PostgreSQL concurrency/history 70.677 ms,
  federation/etcd 15.722 ms, reorg/rebuild 105.403 ms, Text2Cypher 4,594.751 ms,
  and MARA answer contract 6,396.433 ms. This makes the model boundary, rather
  than the databases, the dominant latency in this run.
- Dashboard: critical gates, projection lag, cache-hit ratio, cached-input
  ratio, estimated input-cost reduction, query outcomes, evidence coverage,
  answer accuracy, and live run traces.
- Privacy: prompt/evidence bodies are disabled by default; telemetry carries
  bounded hashes, versions, counts, and outcomes.
- Live run artifact SHA-256:
  `6aed7b517d415cc05c174532c43cdb1f6cdcb52a8170e82da4d75f7a39c23b7c`.
- Reproduce with `scripts/benchmarks/okx_e2e_trace_live.py`; supply the
  PostgreSQL DSN and graph password through `SEOCHO_E2E_DSN` and
  `NEO4J_PASSWORD`, plus `--dataset`, `--bulk-report`, `--output`, and the OTLP
  endpoint. Credentials and prompt/evidence bodies are never written to the
  report or span attributes.

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

### E-013 — S8 reorg and S10 TLS capability

`tags: [live, reorg, rollback, rebuild, tls, enterprise, capability-gate]`

- S8 live result: PostgreSQL retained confirmed, orphaned compensation, and
  replacement revisions. The historical answer remained block-a/sequence 1;
  the current answer became block-b/sequence 3. A destructive DozerDB rebuild
  from PostgreSQL reproduced 3 revisions and exactly 1 canonical revision.
- S8 artifact SHA-256: `665c6aa12bae2c1a1b28f33e05ad225569f042dcb4e784a747e7362ae446f878`.
- S10 implementation: an isolated Neo4j Enterprise 2026.06 TLS profile,
  non-production certificate generator, encrypted-handshake/reload probe, and
  fail-closed capability result. Current DozerDB reported no dynamic reload
  capability and an unencrypted Bolt scheme, so it remains `capability_gated`.
- S10 capability artifact SHA-256: `671576183424f08ef67209809f5ecbe93ccac2f5378595635eb11ae3ff6e672a`.
- Neo4j Enterprise is never started implicitly: a licensed operator must
  explicitly accept the license and provide the TLS profile password.

### E-014 — English customer-query corpus

`tags: [dataset, customer-query, english, market, counterparty, personal-history]`

- The original 10,000-row corpus is retained only as a load-test artifact. A
  later audit found only 60 exact-unique questions (six forms per intent), so
  its 100% routing result must not be cited as natural-language generalization.
- The replacement v2 corpus contains 10,000 exact- and normalized-unique
  English questions across 10 customer intents, 50 semantic template families,
  and five relationships: user-to-self, user-to-market, user-to-network,
  user-to-counterparty, and self-to-prior-self.
- Each row carries required evidence slots, live and memory sources, maximum
  graph hops, and denied inferences. Counterparty questions forbid real-identity
  and wallet-ownership inference.
- Seed workflows are grounded in official OKX and Coinbase help topics for
  order status/fills/slippage, withdrawal confirmation, send/receive delivery,
  funding history, and historical statements. The generated frequency is an
  evaluation hypothesis, not measured support-ticket frequency.
- Replacement corpus SHA-256:
  `3f02b9a8ebc610f276bcd88a9d0e522acad5e805f0c6fbeddd297476cef07938`.
- A separate 300-query boundary corpus is evenly split across ambiguous,
  multi-intent, and out-of-scope requests, with expected actions `clarify`,
  `decompose`, and `reject`. SHA-256:
  `9277226760b65417bfed7118b3fda5deaebe426f44b40569d337b05b77f832b2`.

### E-015 — Unified evaluation observability

`tags: [live, opentelemetry, prometheus, tempo, grafana, evaluation]`

- A bounded evaluation telemetry contract now exports scenario status,
  customer-query counts/accuracy, context reduction, Text2Cypher attempts, and
  capability gates without workspace, user, transaction, query, or prompt
  content in metric labels.
- Live Prometheus verification: template-controlled 10K routing accuracy 1.0,
  causal context reduction 0.8333, S2/S3/S5/S6/S7/S8 status `passed`, and S10
  `capability_gated` with TLS reload capability value 0.
- Live Tempo verification: one `evaluation.run` root and seven child
  `evaluation.scenario` spans were retained under service `seocho-evaluation`;
  the separate live execution trace is recorded in E-008.
- Grafana Evaluation dashboard version 7 contains eighteen panels, including
  customer routing accuracy, context input reduction, S2-S10 status, and
  capability gates plus an embedded Tempo live-run table. Remote path:
  `/d/seocho-critical-agent-memory/seocho-evaluation`.
- Runtime finding: the active Grafana container was an older manually managed
  instance that mounted only its datasource, not repository dashboards. The
  updated dashboard was therefore safely overwritten through the authenticated
  local Grafana API; repository Compose remains the reproducible source.

### E-016 — Customer-query bulk and answer execution

`tags: [live, customer-query, market-data, blockchain, mara, sre, slo]`

- All 10,000 English questions executed through intent routing, source planning,
  freshness policy, and evidence coverage. Coinbase supplied a live BTC spot
  snapshot after the OKX public endpoint returned an HTTP error; Blockstream
  supplied live tip height 957,660. PostgreSQL and DozerDB were live.
- Result: 5,000 supported and 5,000 explicit partial answers, zero unsupported,
  mean evidence coverage 0.8333. Partial outcomes are expected because private
  order, withdrawal, and transfer credentials were not configured; no private
  result was fabricated.
- A bounded MARA `gpt-oss-120b` cohort covered all 10 intents: support-status
  accuracy 100%, missing-source accuracy 100%, leakage zero, p95 3,722.8 ms.
  The first prompt version failed at 50% status accuracy; moving the deterministic
  support status outside model judgment fixed the causal issue.
- Bulk artifact SHA-256: `60fc4c6cbb8baf88fa5af8912d1d7a65c09a6e9bebe7fac94bcded8cea955a68`.
- MARA artifact SHA-256: `878ee8702772c665b9ea47ffd068c1a835aa033784481a3d5df36606bb686b2f`.

### E-017 — Diverse intent routing and boundary governance

`tags: [live-api, dataset-quality, intent-routing, held-out, ambiguity, governance]`

- Diversity gate: 10,000/10,000 exact-unique and normalized-unique questions,
  zero duplicate rate, 50 template families with 101–351 examples each, and
  300/300 unique boundary questions. The quality artifact passed.
- Keyword baseline: evaluation-family accuracy 94.83% and held-out-family
  accuracy 97.27%. The live source pipeline exposed the remaining gap rather
  than hiding it: 4,955 supported, 4,566 partial, and 479 unsupported routes.
  Live Bitcoin height was 957,668 and Coinbase supplied BTC spot; private OKX
  order/transfer/withdrawal sources remained explicitly unavailable.
- Initial MARA prompt-only baseline failed: evaluation intent accuracy 76%,
  held-out 60%, and ambiguous clarification 0%. This invalidated a model-only
  router despite good multi-intent and rejection behavior.
- Candidate hybrid: a deterministic ontology-boundary guard handles known
  ambiguity before the model, while MARA receives intent definitions,
  relationships, and required evidence slots. On 130 live cases it achieved
  evaluation 100%, held-out 90%, ambiguous clarify 100%, multi-intent 90%,
  out-of-scope 100%, and zero ontology-invalid intents. The run passed its
  complete gate; p95 latency was 22.815 s, making fallback rate and provider
  latency explicit production constraints.
- Diversity artifact SHA-256:
  `00ec88833072f9fa4b083655907b82fbae5fb9545c274bc9361bbcf9af467655`.
- Hybrid MARA artifact SHA-256:
  `72d715b5c2b111689ad12e5ab324d00b5b39fb98d9376b293de573525cc3ec1c`.
- Diverse live bulk artifact SHA-256:
  `4e774ea069d37596e47ba8470ee1aaf418a6513c70966458e45de4506fd3af69`.

#### SRE metric decision

- Paging SLIs use production event counters, never sampled traces or one-shot
  evaluation gauges. Evaluation snapshots use 24-hour `last_over_time` panels.
- The initial customer-query objective is a hypothesis: 99% fully supported.
  A multi-window 14.4x fast-burn alert requires both 5-minute and 1-hour breach
  plus at least 20 bad events, preventing low-traffic alert noise.
- Paging: user-visible unsupported/partial rate, silent stale answers,
  disclosure violations, projection stall, dependency loss.
- Diagnosis only: GraphRAG candidates/selected, DB hits, hop count, context
  reduction, Text2Cypher repair, model tokens/cache, agent handoff depth.
- Metric labels remain bounded. Query text, prompt content, wallet, user,
  workspace, transaction, trace, and model output never become metric labels.
- Runtime drift was repaired without deleting the existing TSDB: the active
  Prometheus config now loads the repository rule file and exposes fifteen
  active recording/alert rules. Customer fast-burn paging filters
  `traffic_type="production"`, so this evaluation run cannot page an operator.

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
