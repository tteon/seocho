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
- The final replacement v4 corpus contains 10,000 exact- and normalized-unique
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
  `26c0ba9ab569c7bb62650bcbe67948c6e46fe03c089a834e67276423bdfdd15e`.
- A separate 300-query boundary corpus is evenly split across ambiguous,
  multi-intent, and out-of-scope requests, with expected actions `clarify`,
  `decompose`, and `reject`. SHA-256:
  `39842bf4dde3cbf276f28ebf3040e29d8e136da50ecef22fc0e892612ee2be2c`.

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
- Grafana Evaluation dashboard version 10 contains twenty panels, including
  customer routing accuracy, context input reduction, S2-S10 status, and
  capability gates, diverse-dataset quality, hybrid intent accuracy, and an
  embedded Tempo live-run table. Remote path:
  `/d/seocho-critical-agent-memory/seocho-evaluation`.
- Runtime finding: the active Grafana container was an older manually managed
  instance that mounted only its datasource, not repository dashboards. The
  updated dashboard was therefore safely overwritten through the authenticated
  local Grafana API; repository Compose remains the reproducible source.
- A second runtime-drift audit found that the manually created datasources had
  generated UIDs while repository dashboards reference canonical UIDs
  `prometheus` and `tempo`. This made every panel appear empty despite healthy
  backends. Canonical-UID datasources were added, both health checks returned
  `OK`, and a Grafana `/api/ds/query` request returned the expected v4 values.
  Dashboard version 10 also uses 24-hour `last_over_time` for one-shot
  evaluation snapshots, preventing them from disappearing after Prometheus's
  short instant-query lookback.

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
  zero duplicate rate, 50 template families with exactly 200 examples each,
  and 300/300 unique boundary questions. The v4 quality artifact passed.
- The ontology/template-controlled v4 router passed all 10,000 clear questions.
  This is a closed-universe contract test, not an external customer-language
  generalization claim. The live source pipeline returned 5,000 supported,
  5,000 explicit partial, and zero unsupported routes. Live Bitcoin height was
  957,669 and Coinbase supplied BTC spot; private OKX order/transfer/withdrawal
  sources remained explicitly unavailable.
- Initial MARA prompt-only baseline failed: evaluation intent accuracy 76%,
  held-out 60%, and ambiguous clarification 0%. This invalidated a model-only
  router despite good multi-intent and rejection behavior.
- Candidate hybrid: deterministic ontology guards handle known ambiguity and
  explicit decomposition before the model, while MARA receives intent
  definitions, relationships, and required evidence slots. On 130 live cases
  it achieved evaluation 98%, held-out 90%, ambiguous clarify 100%,
  multi-intent 100%, out-of-scope 100%, and zero ontology-invalid intents.
  The run passed; p95 latency was 24.534 s, making provider latency an explicit
  production constraint.
- The complete 300-query boundary corpus then passed: 100/100 clarify,
  100/100 decompose, 100/100 reject, and zero invalid ontology outputs. The
  200 deterministic guard decisions avoid unnecessary model calls; the 100
  rejection decisions exercise the live MARA governance boundary.
- A stratified 50-query answer cohort across all ten intents achieved 100%
  support-status accuracy, 100% missing-source accuracy, zero leakage, and
  p95 7.294 s.
- Diversity artifact SHA-256:
  `812b4128a7c1bcfd6327b5a6bdbfa27ffb492d0ff723c6f34f8cf30bdc0dd3fd`.
- Hybrid MARA artifact SHA-256:
  `396e16c8996798d1c6e5b9df92e321b7dae7aaaeba8f966a14206947d959bba9`.
- Complete boundary artifact SHA-256:
  `52adfbf89a8496a49455e6e85c1b28d0c2a9733f52496334ded0276afc17ef3f`.
- Answer cohort artifact SHA-256:
  `22b2ad9f81cc94555e1919857131bfd77b00acc5c76cca608df87f495895eda5`.
- Live Tempo roots are linked from Grafana: boundary/intent runs use service
  `seocho-customer-intent-eval`; the 50-answer run is trace
  `c6d1e9201aa99f205533311ac2cd573` under
  `seocho-customer-mara-eval`.
- Diverse live bulk artifact SHA-256:
  `807a5b9da75f7ba920041bccf9bf66cf322b630b2ad17d97da06cf4ca0072ee0`.

### E-018 — Million-revision long-term memory and mixed workload

`tags: [live, postgresql, long-term-memory, point-in-time, concurrency, idempotency, reorg, recovery]`

This is the resume and interview source of truth for the long-term-memory scale
claim. The workload ran against the live PostgreSQL container, not an in-memory
repository or mocked response. The input is deterministic blockchain-style
transaction memory with three revisions per logical transaction, including
time, block, counterparty, confirmation, and state metadata.

Run provenance: PostgreSQL 18.4 from `postgres:18-alpine`, Docker Engine/client
29.5.3, Linux 6.8.0-94 x86_64. The PostgreSQL container had no explicit Docker
CPU, memory, or cpuset limit and shared the host with the rest of the SEOCHO
stack. The reported read and mixed-workload phases did not perform a separate
warmup pass; the database already contained and had queried the 999,999-event
workspace. Results are therefore host-local live measurements, not portable
hardware-normalized capacity claims.

```text
English user/API traffic
        |
        v
weighted scheduler: steady(16) -> spike(64) -> recovery(16)
   |          |             |              |
 current     point-in-time context       atomic writer
 read        read          compaction     + replay/reorg
   +----------+-------------+--------------+
                PostgreSQL authority
       revision + idempotency + outbox + head
                         |
                incremental projection
                         |
            parity + lag drain + RTO gate
```

#### Scale and correctness result

- 999,999 revisions, idempotency receipts, outbox rows, and max sequence matched
  exactly. This proves atomic cardinality at the tested scale; it does not prove
  multi-node PostgreSQL availability.
- At 32 concurrent workers, 2,000 alternating current/point-in-time checks were
  2,000/2,000 correct. Current p95/p99 were 10.33/13.79 ms; historical p95/p99
  were 10.99/14.79 ms; aggregate read rate was 1,317.92 reads/s.
- Selecting the latest state of the same 100 memories reduced serialized context
  from 179,790 to 59,560 bytes (66.87%) with answer-state parity. Rebuilding the
  PostgreSQL projection shadow produced 333,333/333,333 logical memories in
  2.796 seconds.
- The final artifact is a resume of an interrupted 1M load. Consequently its
  ingestion rate and storage delta are zero and must not be cited. The separate
  uninterrupted 100K run measured 23,462.65 events/s and about 2,034.8 bytes per
  event for transactional batch replay; this is not the production single-event
  commit rate.
- Operational finding: the initial 1M ingestion caused PostgreSQL checkpoints
  every 5-13 seconds and `checkpoints are occurring too frequently` warnings.
  WAL/checkpoint tuning is therefore a measured capacity task, not a claimed fix.

Scale artifact SHA-256:
`f9c0aeef78a26ac6f810ece9ef94a082f70830a2a635daba3e4e7d6b5212d6ad`.

#### Production-path mixed workload result

The final acceptance run executed 7,000 closed-loop operations with this fixed
mix: current read 35%, point-in-time read 20%, projection read 15%, context
compaction 10%, atomic live write 8%, duplicate replay 5%, invalid transition
3%, reorg compensation 2%, and projection refresh 2%.

- Steady: 2,000 operations at concurrency 16, 764.25 ops/s, zero unexpected
  errors. Current/PIT/write p95 were 23.13/22.73/49.30 ms.
- Spike: 4,000 operations at concurrency 64, 711.87 ops/s, zero unexpected
  errors. Current/PIT p95 rose to 53.66/52.02 ms. Atomic write p95/p99 rose to
  1,493.11/2,417.29 ms and reorg compensation p95 to 1,386.85 ms.
- Recovery: 1,000 operations at concurrency 16, zero unexpected errors. The
  remaining seven projection events drained in one batch and 0.271 seconds;
  final projection lag was zero.
- Across the run, 382/382 duplicate deliveries were idempotently replayed and
  217/217 invalid transitions were rejected. The authority gained 711 committed
  revisions and ended with revision/idempotency/outbox/head all equal to
  1,002,884.
- Engineering finding: the workspace-scoped monotonic sequence uses one
  `agent_memory_heads` row under `FOR UPDATE`. The write tail-latency jump under
  64-way concurrency exposes a real hot-row boundary. Candidate follow-up is
  leased sequence ranges or partitioned sequence domains while preserving a
  causal-token ordering contract.
- The projection lane here is a PostgreSQL shadow consumer used to isolate
  memory correctness and recovery. DozerDB traversal and transport are evidenced
  separately; this run is not a DozerDB scalability claim. Fixed-count
  closed-loop phases also do not model an open-loop arrival distribution.

Mixed workload artifact SHA-256:
`96189406fb6f06b8a8a7b16f3fdf220ea9ba543cc1a1fd031ff2db3c0bf1757a`.

#### Experiment-driven engineering changes

1. A first projection consumer used `DISTINCT ON ... LIMIT` and could advance a
   watermark past unprocessed sequences. The final runner performs a complete
   parity rebuild, then consumes contiguous sequence batches.
2. A repeat run reused deterministic benchmark idempotency keys. Run UUIDs now
   namespace live writes while duplicates intentionally reuse only a key and
   byte-identical payload from the same run.
3. A point-in-time query before a memory's creation was initially scored as an
   error. The scorer now treats absence as the correct historical state.
4. Projection reads now require exact revision parity; an empty shadow lookup is
   no longer counted as success. The acceptance gate requires authoritative
   cardinality, head/max-sequence parity, zero unexpected errors, and zero final
   projection lag.

#### Sequence scalability follow-up: pool, causal frontiers, and Rust

The original diagnosis was converted into code and rerun against live
PostgreSQL 18.4 with `fsync=on`, `synchronous_commit=on`, and
`full_page_writes=on`. No timing below is mocked. Full memory commits and
allocator-only measurements are intentionally separated.

The compatible v1 path first received two changes that do not weaken its
contract: a bounded Psycopg connection pool and one steady-state
`UPDATE agent_memory_heads ... RETURNING` instead of insert, `SELECT FOR
UPDATE`, then update. At 500 events and concurrency 16, the unpooled full-commit
path measured 275.91 events/s with p95 129.79 ms. The pooled path measured
669.17 events/s with p95 62.59 ms. Both produced exactly 500 revisions,
idempotency receipts, outbox entries, and head sequence.

The larger 10,000-event uniform-wallet run at concurrency 64 measured:

| Full v1 commit client | Throughput | p50 | p95 | p99 | Integrity |
|---|---:|---:|---:|---:|---|
| Python + Psycopg pool | 684.51 events/s | 67.26 ms | 267.71 ms | 414.21 ms | 10,000/10,000 revision, receipt, outbox, head |
| Rust + Tokio/SQLx pool | 875.11 events/s | 53.55 ms | 202.96 ms | 322.45 ms | 10,000/10,000 revision, receipt, outbox, head |

Rust improved throughput by 27.84% and reduced p95 by 24.19% in this host-local
run. It did not remove the database serialization point: Python phase metrics
showed sequence allocation p95 261.37 ms while idempotency lookup, revision
lookup, and memory writes were 0.32, 0.26, and 0.80 ms. Rust is therefore useful
for bounded async scheduling, pool use, payload hashing/serialization, and
projector transports, but a language rewrite is not a substitute for changing
the causal-order design.

Key skew produced a different result. With 5,000 commits targeting one logical
wallet at concurrency 64, Python measured 631.91 events/s and p95 123.05 ms;
Rust measured 806.46 events/s and p95 91.13 ms. The Python aggregate advisory
lock was p95 117.80 ms while sequence allocation was only 0.29 ms. This is the
required per-aggregate ordering boundary: revisions of one wallet cannot be
made concurrent merely by adding sequence shards. A distributed design scales
independent accounts/agents/domains and applies explicit cross-domain causality;
it does not promise parallel mutation of one ordered aggregate.

A deterministic Zipf-1.2 run provided the middle workload. At 5,000 commits and
concurrency 64 it measured 642.56 events/s and p95 199.53 ms. Aggregate-lock
p95 was 188.78 ms while sequence allocation was 3.82 ms. This distribution was
dominated by a small set of wallets, so aggregate-local contention—not the
workspace head—became the capacity boundary. Shard count must therefore be
chosen from observed active-key cardinality and skew, not only total users.

SEOCHO now models that distinction with an opt-in `memory.v2` contract:

```text
workspace
  +-- domain=transaction, shard=02 -> sequence 14
  +-- domain=transaction, shard=11 -> sequence 91
  +-- domain=policy,      shard=00 -> sequence 7
                   |
                   v
CausalFrontier{(transaction,02):14,
               (transaction,11):91,
               (policy,00):7}
                   |
       answer/project only when every shard watermark satisfies it
```

Writers reserve fenced sequence ranges per `(workspace, domain, shard)` and
allocate locally. Lease rows retain owner, fencing token, range start/end, and
acquisition time. A failed writer may leave unused positions; those gaps are
observable and are never interpreted as committed revisions. Projection
watermarks are also per shard, preventing a numerically high position in one
partition from certifying another partition as current.

Allocator-only runs demonstrate the removed allocation RTT but are not
end-to-end write claims. At 10,000 uniform allocations/concurrency 64, one
128-position leased domain measured 23,433 allocations/s and left 112 reserved
positions unused. Sixteen shards with 16-position leases measured 17,794/s and
left 144 unused; 64 shards measured 19,758/s and left 512 unused. In the earlier
500-event run, 64 shards with 128-position leases left 7,180 unused positions.
The engineering conclusion is that lease and shard counts must follow observed
key distribution, acceptable gap budget, restart rate, and projection fan-out;
larger values are not automatically better.

The repository emits bounded phase histograms for `connection_scope`,
`idempotency_lookup`, `aggregate_lock`, `sequence_allocate`,
`revision_lookup`, and `memory_writes`. Live OTLP export was verified in
Prometheus under `seocho_memory_commit_phase_duration_milliseconds_*`, and the
Grafana `SEOCHO Memory Consistency` dashboard exposes phase p95. No workspace,
wallet, transaction, prompt, or trace identifier is a metric label.

The optimized repository was also rerun through the existing 7,000-operation
mixed workload on the 1,002,884-revision workspace. At concurrency 16 it
measured 1,265.08 ops/s with current/PIT/write p95 of 8.01/8.09/59.18 ms. At
concurrency 64 it measured 1,214.97 ops/s with current/PIT/write p95 of
44.15/42.46/910.47 ms. Recovery returned to 1,377.03 ops/s; five projection
events drained in one batch and 369 ms. Across the run 382 duplicate deliveries
were idempotently replayed, 217 invalid transitions were rejected, 711 new
revisions were committed, and final revision/idempotency/outbox/head/watermark
all equaled 1,003,595. Compared with the prior unpooled run, spike throughput
rose from 711.87 to 1,214.97 ops/s and write p95 fell from 1,493.11 to 910.47
ms. This is a same-host before/after, not a hardware-normalized capacity claim.

Raw artifact hashes:

- 500-event pool/strict/allocator smoke:
  `44e2a402c6577f8d7fdf7c51afab2a6d688e03860d1311314929d3adbeebfe05`;
- 10K uniform Python/allocator:
  `8e607d89915672158c575fd406408cc5074110ae4144e5c60af64dadeed8db36`;
- 10K uniform Rust:
  `5dd1b3eff92fc4c40853c34ac3dec0ae1904d74c0e053a261d61f97378a25639`;
- 5K hot-wallet Python/allocator:
  `0c6bf829e95f65f1971902e49fe8ea37a0041402fb3c3dabe14b7914dccde122`;
- 5K hot-wallet Rust:
  `4cb0eabf80bd4bbe593a95ba85bb2088f30f97d7049bbf08d28c77a2824f0b13`;
- OTLP phase export qualification:
  `5b0359eddc4cd899e71736a19984506d07af7d74a1eb7444efdf71d5844dd896`;
- Zipf-1.2 Python/allocator:
  `fd5dd7980d401acb52c0ccca859c2361de1aefd145e8bba0d4945350a724bdfc`;
- optimized 7K mixed workload:
  `9de5f0d7153ec413d7b9038ceeea7dd51a08282276531474398c05f2ea961b40`.

The production default remains strict v1. V2 is an explicit alpha contract
until full revision/outbox integration, writer-kill recovery, leased-gap audit,
Zipf skew, shard-aware projector claiming, and reorg rebuild parity pass live
qualification. This limitation is part of the result, not hidden backlog.

#### Resume-ready bullets

- Architected and live-tested append-only agent long-term memory on PostgreSQL
  with atomic revision, idempotency, outbox, causal sequence, point-in-time read,
  and rebuildable graph-projection contracts; verified 999,999-row parity and
  2,000/2,000 correct temporal reads at 32-way concurrency.
- Designed a 7,000-operation blockchain memory workload spanning current and
  historical retrieval, context compaction, concurrent writes, duplicate
  delivery, invalid state transitions, reorg compensation, and projection
  recovery; preserved authoritative integrity with zero unexpected errors and
  recovered seven lagging projection events in 271 ms.
- Diagnosed a workspace sequence-head contention boundary from live measurements:
  atomic-write p95 increased from 49 ms at concurrency 16 to 1.49 s at
  concurrency 64, motivating leased/partitioned sequence allocation rather than
  presenting an unqualified scalability claim.
- Improved equal-semantics strict memory throughput from 275.91 to 669.17
  events/s with connection pooling and atomic head allocation, then built a
  Tokio/SQLx parity probe that reached 875.11 events/s at 10K/64 writers while
  preserving exact revision, idempotency, outbox, and causal-head cardinality.
- Designed versioned causal frontiers, deterministic shard routing, fenced
  sequence leases, and shard-local projection watermarks; quantified the
  throughput-versus-unused-range trade-off and kept the scalable policy opt-in
  until crash/recovery and full-write qualification complete.
- Reduced same-memory long-history context serialization by 66.87% while
  preserving latest-state answer parity, and connected memory/projection SLIs
  to the existing OpenTelemetry, Prometheus, Tempo, and Grafana evidence plane.

#### Full answer-generation status

The background MARA run completed 10,000 English answer calls with no transport
errors, but did not pass its quality gate: support-status accuracy was 99.91%,
missing-source accuracy 99.58%, and p95 provider latency 29.218 seconds. This is
retained as a provider/prompt scorer improvement item rather than cited as a
successful E2E result. Artifact SHA-256:
`cbc03265b75dca6ca746c4c79ee1ed4692361ab5c3c6176e870ec785c04704f0`.

#### Memory-framework comparison (live PostgreSQL and Cognee/MARA)

The framework comparison uses one deterministic synthetic blockchain
transaction-lifecycle corpus. Synthetic labels are explicit; the runs are real
executions against the named services rather than in-memory or mocked latency
claims.

- SEOCHO and LangGraph shared one live PostgreSQL 18 instance and the same 30
  ordered events. SEOCHO applied 30/30 events at 107.64 events/s and answered
  29/29 supported current and point-in-time cases correctly. LangGraph Store
  applied 30/30 at 717.62 events/s and answered its 10/10 supported current
  cases correctly; 19 point-in-time cases were explicitly unsupported and were
  not converted into false failures. Duplicate replay was rejected by both,
  natively by SEOCHO and through an adapter receipt in the LangGraph run.
- The same contract was then expanded to 10,000 events and 500 sampled logical
  memories. SEOCHO applied 10,000/10,000 at 99.03 events/s, scored 1,499/1,499
  current, point-in-time, and pre-creation reads correctly, and recorded
  retrieval p50/p95 of 7.41/8.14 ms. LangGraph applied 10,000/10,000 at 397.07
  events/s and scored its 500/500 supported current reads correctly; the other
  999 temporal cases remained unsupported. SEOCHO therefore paid roughly a
  four-times write-throughput cost in this single-client run for the stronger
  revision/outbox/idempotency and temporal-read contract.
- This is a capability/semantic comparison, not a claim that the systems do
  equal work. SEOCHO's lower write throughput includes append-only revisions,
  atomic idempotency and outbox state, provenance, and rollback/rebuild
  contracts. The LangGraph baseline overwrites current values and does not
  expose point-in-time revision reads in this adapter.
- Cognee 1.3.0 was run through its real temporal ingestion and recall path with
  MARA MiniMax-M2.7, local FastEmbed (`all-MiniLM-L6-v2`, 384 dimensions), and
  an isolated Neo4j-compatible graph with APOC. A three-event smoke returned
  2/2 correct current/historical answers with zero disclosure leakage. At 30
  events across ten logical memories, the sampled result was 3/6 (50%): two
  latest-state answers selected superseded states and one historical request
  refused despite the ingested event. Ingestion was 0.452 events/s and recall
  p50/p95 were 10.88/20.00 seconds.
- The Cognee result is not a broad leaderboard. It is a small domain-specific
  qualification showing that successful graph construction and a passing tiny
  smoke do not guarantee temporal correctness as histories become denser. It
  motivates using generative graph memory for semantic discovery while keeping
  transaction state, revision order, idempotency, and point-in-time truth in a
  deterministic authoritative layer.

##### Deep interpretation: what was actually compared

The experiment did not ask which framework wins a generic memory leaderboard.
It asked which guarantees are appropriate for three different responsibilities
inside a production transaction agent:

1. **Authoritative state memory:** can the system reproduce what was believed
   at a specific causal point, reject duplicate delivery, retain corrections,
   and rebuild a serving projection without inventing history?
2. **Application state memory:** can the system store and retrieve the latest
   value cheaply when the application does not need an immutable revision
   history?
3. **Generative semantic memory:** can the system infer entities and
   relationships from text and retrieve useful context without rereading the
   complete source history?

These are related but not equivalent workloads. A current-value store performs
less work than an append-only transaction ledger, and an LLM-created knowledge
graph performs more probabilistic work than either structured store. Raw
throughput therefore cannot be interpreted without the capability matrix.

| Decision criterion | SEOCHO/PostgreSQL | LangGraph Store adapter | Cognee temporal graph |
|---|---|---|---|
| Latest structured state | native | native | generated answer |
| Point-in-time state | native | unsupported | probabilistic |
| Pre-creation absence | deterministic | unsupported | not qualified |
| Immutable revisions | native | current value only in this adapter | graph-derived memory |
| Duplicate delivery | atomic receipt | two-write adapter receipt | not qualified as a transaction guarantee |
| Projection outbox | same transaction as revision | absent | internal pipeline behavior |
| Provenance | required revision field | copied application field | extracted graph/source metadata |
| Rollback/rebuild basis | ordered authoritative log | unsupported in adapter | regenerate/re-ingest |
| Semantic association | graph projection/adapter | unsupported in baseline | core strength |
| Best-fit role | transaction truth | lightweight current state | semantic discovery/context |

##### Why SEOCHO was slower, and why that is not automatically bad

In the 10K single-client run, LangGraph Store wrote approximately 397 events/s
while SEOCHO wrote approximately 99 events/s. This is a real cost and must not
be hidden. However, the write paths did not provide the same guarantee:

```text
LangGraph baseline write
  current value -> application receipt implemented by the benchmark adapter

SEOCHO authoritative write
  lock causal head
    -> append immutable revision
    -> insert idempotency receipt
    -> insert projection outbox event
    -> advance causal sequence
    -> commit all-or-nothing
```

SEOCHO's approximately four-times lower throughput bought atomic coordination
between four pieces of state that otherwise fail independently. If a process
dies after a current-value write but before an idempotency receipt, a replay can
be applied twice. If it dies after the receipt but before the graph projection
event, the serving graph can remain silently stale. The transactional outbox
removes this dual-write gap. For an exchange, custody, compliance, or settlement
workflow, this correctness cost can be justified; for a disposable chat
preference, it usually cannot.

The result therefore supports a tiered memory policy rather than putting every
memory into the expensive path:

- transaction lifecycle, authorization decision, policy version, and answer
  provenance use authoritative revisions;
- ephemeral agent scratch state and replaceable UI/session state may use a
  current-value store;
- documents, conversations, and weak semantic associations may use generative
  graph memory, but cannot override authoritative transaction fields.

##### What the temporal accuracy result means

SEOCHO answered all 1,499 supported cases in the 10K qualification. The cases
included latest state, a prior revision, and a sequence before the memory
existed. The last case is operationally important: returning the first known
state for a time before creation is a form of future-information leakage.

LangGraph's reported accuracy was also 100%, but only over 500 supported
current-value cases. It would be misleading to write `SEOCHO 100% versus
LangGraph 33%`; unsupported point-in-time operations were deliberately excluded
from LangGraph's denominator. The correct interpretation is:

- both systems were correct for their shared current-state capability;
- SEOCHO additionally passed 999 temporal cases that the baseline adapter could
  not express;
- LangGraph delivered higher throughput and lower current-read latency because
  it retained a narrower state contract.

This capability-adjusted scoring is essential for honest infrastructure
evaluation. Missing functionality is a product-fit limitation, not a fabricated
wrong answer, while a supported operation returning the wrong revision is a
correctness failure.

##### What the Cognee failures reveal

Cognee successfully transformed the corpus into a temporal knowledge graph and
answered both questions in the three-event smoke. That establishes real
provider, embedding, extraction, graph-write, retrieval, and answer-generation
connectivity. It does not establish scale or temporal reliability.

At 30 events, two latest-state questions returned superseded states and one
historical question refused despite the event being present in the ingested
corpus. These are qualitatively different failure modes:

- **revision selection error:** relevant facts exist, but the retrieval or
  reasoning stage does not enforce the maximum valid revision;
- **temporal retrieval miss:** a historical fact exists, but timestamp/entity
  alignment does not surface it to the answerer;
- **unsupported-evidence response:** the model generates an inability statement
  instead of grounding itself in the stored graph;
- **latency amplification:** extraction, graph retrieval, and LLM synthesis
  produced an approximately 20-second p95 in the sampled run.

The outcome does not mean Cognee is generally poor. It means a generated memory
graph optimizes a different objective: discovering and prioritizing semantically
related context. Blockchain state transitions require a total or explicitly
partitioned causal order, validity bounds, canonical/reorg status, and exact
revision selection. Those constraints should be applied before generative
answering, not inferred anew by the model for every question.

A safe integration would therefore use Cognee-like memory only as a candidate
context source:

```text
semantic memory candidates
        +
authoritative PostgreSQL revision at required causal token
        +
DozerDB path evidence at a sufficient projection watermark
        |
        v
ontology/policy filtering -> bounded prompt -> answer + provenance
```

If semantic memory disagrees with the authoritative revision, the deterministic
revision wins and the disagreement becomes an observability/evaluation event.

##### Scalability result and current bottleneck

The 10K comparison measured a single-client structured baseline, so it is not a
concurrent production capacity claim. The separate mixed workload exposed the
more important concurrency boundary: at concurrency 16, atomic-write p95 was
approximately 49 ms; at concurrency 64 it increased to approximately 1.49
seconds, while current and point-in-time read p95 remained near 52-54 ms.

This isolates the bottleneck to the workspace-scoped causal head locked with
`FOR UPDATE`, rather than graph traversal or LLM generation. The design provides
simple total ordering but creates a hot row under many writers. Possible
improvements must preserve a clear causal contract and be measured rather than
assumed:

- lease ranges of sequences to writers, accepting bounded gaps;
- partition causal domains by account, agent, transaction, or chain;
- batch ordered event commits where the producer already establishes order;
- separate bulk historical import from latency-sensitive online commits;
- apply backpressure when outbox age or graph projection lag exceeds its SLO.

The correct production statement is therefore that SEOCHO demonstrated
accurate temporal reads and recoverable state at the measured loads, while the
64-writer experiment identified a real sequencing bottleneck. It is not yet a
claim of horizontally scalable multi-writer PostgreSQL authority.

##### Architecture decision derived from the evidence

No evaluated framework should own every memory responsibility. The evidence
supports the following separation:

```text
PostgreSQL / SEOCHO authority
  revisions, idempotency, causal order, policy decision, audit, rollback
                   |
                   | transactional outbox + watermark
                   v
DozerDB graph serving projection
  bounded multi-hop paths, agent-agent relations, GraphRAG evidence
                   |
                   | selected and freshness-checked evidence
                   v
Generative semantic memory
  conversation/document association, summarization, context candidates
                   |
                   | ontology and disclosure guardrail
                   v
MARA answer generation
  answer, insufficiency status, provenance references
```

This separation makes the graph disposable and replayable, keeps etcd limited
to small control-plane pointers and fencing, and prevents an LLM-generated
memory from silently rewriting transaction truth. It also allows cost-sensitive
customers to disable semantic memory or choose a cheaper provider without
changing authoritative semantics.

##### Production selection guidance

- Choose the SEOCHO authoritative path when a wrong revision, duplicate action,
  missing audit trail, or unrecoverable reorg is more expensive than lower write
  throughput.
- Choose a LangGraph-style current store when only the latest replaceable state
  is required and the application owns any additional durability contract.
- Choose a Cognee-style semantic layer when entity discovery, cross-document
  association, and context reduction matter, while keeping deterministic fields
  outside its authority.
- Combine the layers when an agent must answer natural-language questions about
  auditable transactions: deterministic state first, graph paths second,
  semantic expansion third, and answer generation last.

For OKX-like workloads, this maps to a practical rule: balances, transaction
status, authorization, policy, and compliance evidence are authoritative;
wallet relationships and bounded paths are graph projections; conversational
preferences and document associations are semantic memory. An answer is safe
only when its causal token, graph watermark, ontology/policy version, and source
references are visible in one trace.

##### Limits of the conclusion

- The structured 10K corpus was deterministic and synthetic, although all
  writes and reads executed against live PostgreSQL. It supplements rather than
  replaces replay of a larger real chain/account history.
- LangGraph was evaluated through a deliberately small structured adapter, not
  every LangGraph persistence or checkpoint pattern.
- Cognee's 30-event result is too small for a global quality or cost ranking and
  includes MARA provider latency. It is evidence of a domain-specific temporal
  failure mode, not a universal framework verdict.
- The runs did not normalize every internal write, model token, hardware limit,
  or cache state, so cross-framework latency is diagnostic rather than a formal
  price/performance benchmark.
- Multi-node PostgreSQL, graph cluster failover, and repeated process-kill RTO
  distributions remain future gates.

The defensible conclusion is consequently narrow but valuable: SEOCHO's
deterministic revision layer provides the temporal and recovery semantics that
the lighter current-value and generative-memory baselines did not provide in
this qualification, at a measured throughput and concurrency cost that is now
visible and actionable.

The 30-event PostgreSQL comparison was rerun after fixing the scorer for reads
before a memory's creation: absence is the correct historical result only when
the requested sequence precedes the first revision. Provider metadata also
records that MARA's chat model endpoint was used for Cognee extraction/answering
while embeddings remained local; the probed MARA MiniMax endpoint returned an
unsupported-embeddings response.

Local evidence artifact SHA-256 values:

- 30-event SEOCHO/LangGraph:
  `1ac8df776f387b61e5e481e10635d620f2f6a79483caedcaa96470e3e87958b6`
- 10K-event SEOCHO/LangGraph:
  `12d602a38931779aecb540c96c33f0ebfee62c92c7f00fc14aba0e1df15e9d7c`
- 30-event Cognee:
  `d50b9be1134c7aab6d733f9565915cf3a1fe471308418c380c40ac5106865be8`
- 3-event Cognee/Neo4j-compatible smoke:
  `1888bceb3ea2bffeb131da70e2a895b5c637af73d18b7ce9629eee3e9d5810d9`

Reproduce the structured comparison after `make memory-up`:

```bash
uv run --extra postgres --extra memory-bench \
  python scripts/benchmarks/memory_framework_compare_live.py \
  --dsn "$SEOCHO_POSTGRES_DSN" --events 10000 --sample-memories 500 \
  --frameworks seocho,langgraph --output /tmp/memory-framework-10000.json
```

Cognee is intentionally isolated from SEOCHO's product dependencies. Run its
published package in a disposable environment, point `LLM_API_KEY` at a MARA
key, and use `scripts/benchmarks/cognee_blockchain_memory_live.py`; do not add
Cognee, its graph backend, or its vector stack to the default SDK installation.

### Cross-functional lessons learned

`tags: [resume, sre, data-engineering, distributed-systems, ai-engineering, ontology]`

This section translates the implementation and live results into the four
engineering perspectives expected from a production AI-infrastructure role.
Each lesson separates a measured result from a proposed next improvement.

#### SRE — observe causal stages, not only end-to-end latency

An agent answer crosses intent routing, authoritative memory, graph freshness,
Text2Cypher, retrieval, context assembly, and model generation. A single total
latency cannot identify which consistency or dependency boundary failed.

The Grafana/Prometheus/Tempo surface is therefore organized into:

- user SLIs: supported/partial/unsupported outcomes, E2E p50/p95/p99, stale
  answer count, disclosure violations, and time to first/final token;
- memory SLIs: current/PIT read latency, atomic commit latency, duplicate and
  invalid-transition outcomes, causal-read failures, and revision/idempotency/
  outbox cardinality parity;
- projection SLIs: authoritative sequence minus serving watermark, oldest
  outbox age, projection batch duration, fencing rejections, replay count, and
  rebuild/recovery RTO;
- GraphRAG diagnostics: hop count, rows, DB hits, scan/Cartesian/variable-expand
  operators, candidate-to-selected evidence ratio, and Cypher repair count;
- model diagnostics: provider/model latency, timeout/rate-limit/retry, input/
  output/cache tokens, cache-hit ratio, and structured-output failures;
- governance diagnostics: ontology/policy/prompt version, missing evidence,
  redaction count, and current-memory leakage into historical answers.

The 64-worker spike demonstrates why this decomposition matters: current and
PIT p95 remained approximately 54 and 52 ms, while atomic-write p95 increased
to 1.49 seconds. The traceable cause is the workspace-scoped monotonic sequence
head locked with `FOR UPDATE`, not the model or graph database. Follow-up design
options are leased sequence ranges or partitioned causal domains. Paging must
use production event counters; evaluation snapshots and high-cardinality user,
wallet, prompt, or trace identifiers remain diagnostic-only and never metric
labels.

#### Data engineering — authority and serving projection are different planes

Blockchain and agent transactions evolve through pending, confirmed, replaced,
failed, and reversed states. Overwriting the latest row destroys reorg and
historical-answer evidence. PostgreSQL therefore commits a memory revision,
idempotency receipt, projection outbox entry, and causal sequence atomically.
The 999,999-event run verified exact parity across all four counts and 2,000 of
2,000 correct current/PIT checks at concurrency 32.

The graph is a rebuildable serving projection, not a second source of truth.
The transport roles are deliberately distinct:

- Arrow IPC is the typed in-memory/streaming projection batch;
- Parquet is the compressed checkpoint, audit, replay, and rebuild artifact;
- Protobuf/OTLP transports telemetry and small control messages rather than
  being the primary bulk property-graph format;
- bounded Bolt writes remain the capability-gated fallback.

Every projection row needs more than business properties: `workspace_id`,
`memory_id`, `memory_sequence`, `revision`, `schema_version`, ontology/policy
version, provenance, payload hash, canonical state, event/ingest time, and typed
relationship endpoints. A watermark advances only after graph commit and parity
validation. The first mixed runner exposed why: `DISTINCT ON ... LIMIT` could
select sparse memories and incorrectly skip intermediate sequences. The final
consumer rebuilds a parity baseline and then consumes contiguous sequence
batches.

The 1M load also produced PostgreSQL warnings that checkpoints occurred every
5-13 seconds. This is recorded as a capacity finding, not a solved claim. The
next measured sweep should cover WAL/checkpoint sizing, COPY replay versus live
single-event commit, outbox batch size, time/workspace partitioning, index write
amplification, and projector backpressure.

#### Distributed systems — ontology metadata is versioned control-plane state

Moving nodes and relationships from an RDBMS to a GDBMS is insufficient when
their meaning, visibility, and query constraints vary by ontology and policy.
SEOCHO separates the planes:

```text
PostgreSQL authority -> Arrow/Parquet outbox -> DozerDB/Neo4j projection
                              ^
                              |
etcd: active ontology/policy/prompt pointers, owner lease, fencing token,
      projection watermark pointer
```

etcd never stores transaction bodies, prompts, wallet addresses, or evidence.
It stores small coordination records such as active ontology/policy versions,
projector ownership, fencing tokens, and watermark pointers. A projector checks
these records before writing and must acknowledge with the current fencing
token, preventing a stale worker from overwriting a newer projection.

Graph records carry `_workspace_id`, `_memory_sequence`, `_schema_version`,
`_ontology_context_hash`, provenance references, validity bounds, canonical
status, and disclosure class. Retrieval combines the PostgreSQL causal token,
graph watermark, and active ontology hash in one Context Envelope. A graph that
is behind the required token yields `partial` or `stale`, not an apparently
complete answer. In the final mixed run, seven remaining projection events were
drained in one batch and 271 ms. The lesson is bounded staleness: operators and
agents must know how far the projection is behind and whether it is safe to
answer, rather than relying on an undefined promise of eventual consistency.

#### AI engineering — prompt optimization is structural context stability

Repeated system instructions, ontology definitions, tools, Text2Cypher rules,
answer schema, and disclosure rules form a byte-stable prefix. The user request,
selected revisions, graph evidence, freshness, and missing slots form a variable
suffix. Prefix identity uses prompt, ontology, tool-schema, and policy hashes;
timestamps, request IDs, user metadata, and random ordering stay out of it.

The relevant measurements are stable/variable token counts, cache-read/write
tokens, exact-prefix reuse, cold/warm latency, input-cost reduction, context
compression, answer parity, and provider-specific structured-output success.
Compacting the same 100 memories from 300 raw revisions reduced serialized
context from 179,790 to 59,560 bytes (66.87%) while preserving latest-state
answer parity.

Load balancing should also be prompt- and workload-aware: group requests sharing
a cache domain, route structured Text2Cypher to models with measured schema
adherence, bound long generations with semaphores, and keep deterministic
support/disclosure decisions outside the model. The 10K MARA run had no
transport errors but p95 latency of 29.218 seconds and missed the strict answer
quality gate. Provider availability is therefore not equivalent to production
answer correctness.

#### Ontology/model choice changes graph topology and RAG behavior

The same source can become a shallow `Agent -> SENT -> Bitcoin` graph or an
event/revision graph connecting intent, execution agent, blockchain transaction,
block, and counterparty. The first favors cheap shallow lookup; the second
supports state transition, provenance, reorg, and temporal reasoning at higher
ingestion and traversal cost. An overly strict ontology can reduce extraction
recall, while a permissive ontology increases ambiguity and Text2Cypher search
space.

The fair ablation holds source corpus, gold facts, query set, answer model, and
context budget constant while varying extraction model, ontology profile, and
graph representation. It reports entity/relation precision and recall,
ontology-invalid edges, duplicates/orphans, density, required hops, retrieval
recall/evidence precision, temporal leakage, Cypher repair, groundedness,
abstention, tokens, latency, and cost. Graph construction quality must be scored
before answer generation so a strong model cannot hide a weak graph and a weak
model cannot obscure good retrieval.

The combined lesson is that production agent memory is not “send more history
to an LLM.” It is the joint design of authoritative temporal history,
rebuildable serving graphs, versioned ontology control state, cache-aware
context assembly, and causal observability.

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
