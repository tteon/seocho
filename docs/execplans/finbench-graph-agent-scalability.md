# ExecPlan — FinBench Graph-DB ↔ Agent Scalability & Middleware Showcase

## Purpose / Big Picture

**The question: as a graph-backed agent scales, which axis actually starts to
matter — and what should an infrastructure owner do about it?**

Everything here exists to answer that. We vary one axis at a time — data volume,
plan shape, schema grounding, query routing, load path, page cache, container
memory, model — against the same graph and the same questions, and rank the axes by
how much they moved. Comparisons that show up along the way (template vs generated
Cypher, Cypher vs SQL) are instruments for reading those axes, not the subject.

The vehicle is a synthetic LDBC-FinBench-style AML graph loaded into the primary
serving DB (DozerDB) and queried through the Graph Agentic RAG path. Why synthetic
(DuckDB) instead of LDBC FinBench datagen: the official datagen is Spark/JVM based.
A DuckDB generator removes that dependency, makes scale a parameter (`--sf`), and —
decisively — lets us *plant* AML patterns with reserved IDs so gold answers are
known exactly and agent output can be scored.

A run is declared in one file (`examples/finbench/bench.yaml`) and the report echoes
every parameter next to the numbers it produced, so any result traces back to the
settings that made it.

## What actually mattered, ranked by measured effect

Every axis below was measured on this host (16-core / 62 GiB) against an SF1000 graph
(3.3M nodes / 19.6M relationships / ~2.9 GB store) and the same question set.

> **Scope note — three distributions, not one.** The generator took *size* as a parameter
> and left every *distribution* to uniform sampling, and all three distributions that
> matter were therefore absent. Measured at SF1000: degree **max 31** (FinBench expects
> hubs in the millions); edge multiplicity **max 2** over 10M edges, 14 duplicate pairs;
> average local clustering **exactly 0.000** against 0.1–0.5 in real networks. Degree and
> multiplicity are now parameters (`--hub-skew`, `--dup-share`) and the memory rows below
> carry both the degree-less and the power-law number. Clustering is measured and published
> but **not yet generated** — see the structural-properties section. Every snapshot now
> ships a `structural_profile` in its manifest, so a result can be read against the data
> that produced it.

| rank | axis | kind | effect when varied |
|---|---|---|---|
| 0 | **Degree distribution** (uniform vs power-law, max degree 31 → 158,315) | data | **unbounded, but only for non-early-terminable questions** — an aggregate goes 45 ms → *timeout*; a `LIMIT`-able question is unaffected |
| 1 | **Plan shape** (label + indexed property vs unlabeled match) | software | **264,005x** db hits, **411x** warm p50 — *identical answers* |
| 2 | **Load path** (bolt transactions vs `neo4j-admin import`) | software | **4,740x** throughput; 38 h → 36 s at SF1000 |
| 3 | **Query routing** (`RouteProfile` policy book) | software | 33M → 490 db hits; accuracy 89% → **100%** |
| 4 | **Schema grounding** (full ontology vs labels only) | software | sargable 0% → 75%; accuracy 0% → 67% |
| 5 | **Data volume alone** (SF1 → SF1000, code frozen) | data | accuracy 100% → **67%** |
| 6 | **Model / provider** | software | 89–100% accuracy; provider latency variance 33.8 s → 2.2 s *on identical code* |
| 7 | **Page cache size** | hardware | degree-less graph: **nothing** (6 GiB → 128 MiB, p50 2.2 → 2.1 ms). Hub graph, large neighbourhood: **2.3x** (6 GiB → 256 MiB, p50 40 → 94 ms / 87 → 200 ms) — a shallow curve, not a cliff |
| 8 | **Container memory** | hardware | **no gradient above the floor** on either graph; binary failure below it (4 GB runs, 2 GB will not start) |
| 9 | **CPU** | hardware | never the constraint — 16 cores sustained 673k rel/s |

**The insight: every axis that mattered was software, and the hardware axis is a
floor rather than a curve.** The two knobs an infrastructure team would normally
reach for first — more RAM, more cores — produced no measurable improvement, while
a query the planner could not index cost five orders of magnitude more work for the
same answer.

What that means operationally:

- **Provision to the floor; size the cache to the largest neighbourhood, not the store.**
  For SF1000 the floor is "works at 4 GB, does not start at 2 GB" — a threshold, not a
  curve. Above it, extra RAM buys nothing *until* a query's neighbourhood stops fitting
  in the page cache, and then it buys a bounded 2.3x. So the sizing input is the biggest
  working set you actually serve, which `curate_parameters.py` computes offline from the
  data; sizing to total store size over-provisions, and sizing to a small-neighbourhood
  benchmark under-provisions.
- **Do not size hardware before the software path is fixed.** The "do we need a
  128-core / 512 GB instance?" question resolved to *no* — the bottleneck was bolt
  round-trips. Sizing against an unfixed path budgets for the wrong machine.
- **Alert on plan shape, not on latency.** At SF1 the failing shape is 4.3 ms vs
  17 ms and unwarmed it reads as *nothing* (20 ms vs 19 ms) — while db hits already
  differ 269x. Db hits is the leading indicator; latency at low scale is not, so
  the Grafana panel to watch is sargability and db hits per answer.
- **Bound the traversal, not the result — and check the engine can.** `max_result_rows`
  only protects questions where an arbitrary subset is a valid answer; aggregates and
  ranked queries walk the whole neighbourhood regardless. But the obvious fix (per-hop
  `ORDER BY ... LIMIT`) measured 70,000x *worse* on a hub, because sorting breaks the
  laziness that was the real protection. Truncation needs engine support to be a
  mitigation rather than an amplifier.
- **Accuracy will not warn you.** Both plan shapes return identical answers, and
  100% accuracy at SF1 concealed that 44% of that model's queries full-scan. The
  thing that breaks at scale is invisible to the metric most benchmarks report.
- **Budget attention where the time goes.** The graph engine is 0.3–0.9% of
  end-to-end agent latency (122 ms vs 37,083 ms). Tuning the database to serve an
  agent is optimizing the wrong 1%; the plan-shape work matters for *cost and
  survivability at scale*, not for felt latency.

The secondary thesis (Track 7 — the graph/ontology are the durable asset, the LLM
is swappable compute) is confirmed as a by-product: rows 3, 4 and 6 together show
the graph and ontology never changed while models were swapped, and the ontology
contributed more to accuracy than the model choice did.

Why synthetic (DuckDB) instead of LDBC FinBench datagen: the official datagen is
Spark/JVM based. A DuckDB generator removes that dependency, makes scale a
parameter (`--sf`), and — decisively — lets us *plant* AML patterns with reserved
IDs so gold answers are known exactly and agent output can be scored.

## What this data is, precisely

Stating it exactly, because "LDBC FinBench data" would be wrong and the imprecision has
already cost this experiment three separate rediscoveries.

**This is a controlled synthetic graph modelled on the LDBC FinBench schema — not LDBC
data.** The real datagen was never run. Recorded at the time, the reasons were: the official
datagen is Spark/JVM based, scale is a parameter, and — the one marked "decisively" — planting
patterns at reserved ids makes gold answers exact.

Reviewed against what happened, only the third held.

- *Spark dependency* was overstated. The datagen repo documents a local run
  (`scripts/get-spark-to-home.sh`, `scripts/run_local.sh`); no cluster is needed.
- *Scale as a parameter* is not a differentiator — the real datagen takes a scale factor too.
- *Exact planted gold* is real, and double-edged: planting at reserved ids also made the case
  set blind to the graph around it. Case anchors measured degree 2–26 against a graph maximum
  of 158,315, so the questions could not see the distribution at all.

A fourth benefit appeared later and was **not** the reason at the time, so it does not
retroactively justify the choice: a generator under our control can hold volume fixed while
varying distribution. That control arm — identical edge counts in both arms at every scale —
is what makes the central result defensible, and real LDBC data cannot provide it, because
SF1 and SF10 differ in size *and* shape at once.

### Schema correspondence

FinBench declares 5 vertex types and 9 edge types. After adding the party and device layers:

| FinBench | here | note |
|---|---|---|
| Person, Company, Account, Loan | same | |
| **Medium** | Medium | login device; distinct from our `Channel`, which is a payment rail |
| — | Channel | local extension (전자금융거래법 §2 + FATF); FIBO declares no PaymentMethod taxonomy |
| transfer, own, deposit, repay | same | the account layer, modelled from the start |
| **apply, invest, guarantee, withdraw, signIn** | added | the party and device layers |

**All 9 edge types are now present.** Before this, four were, and the five missing ones were
exactly the edges connecting accounts to the parties behind them — so a question like "are
these two accounts under common control" could not be *expressed*, let alone answered, and the
experiment had no multi-layer case at all. That gap was invisible until the schema was compared
side by side.

Deliberate remaining differences: `isBlocked` is static here (`flagged` on Account only) rather
than a dynamic attribute on four vertex types; FinBench's `payType`/`goodsType` on transfer are
replaced by `channel`/`channel_risk`; degree distribution, edge multiplicity and triadic
closure are generator *parameters* rather than intrinsic, which is the point of the control arm
and also the reason each had to be retrofitted.

### The multi-layer case

Planted and verified: two accounts transferring between themselves, whose owners guarantee each
other, which share a login device. Each layer is unremarkable alone — people transfer money,
partners guarantee each other, households share devices — and the conjunction is the finding.
FATF and FFIEC both treat common control behind nominally unrelated parties as a core
concealment pattern.

```cypher
MATCH (a:Account)-[:TRANSFER]->(b:Account)
MATCH (pa)-[:OWN]->(a), (pb)-[:OWN]->(b)
MATCH (pa)-[:GUARANTEE]-(pb)
MATCH (m:Medium)-[:SIGN_IN]->(a), (m)-[:SIGN_IN]->(b)
```

Returns exactly one match at SF1 — the planted pair — so gold is exact and there are no
distractors yet. Four edge types across three layers, which is the shape a single homogeneous
edge table cannot recurse over.

## Context and Orientation

- Branch `exp/finbench-scale` (on top of `feat/seocho-8track-consolidation`).
- Serving DB: DozerDB (`graphstack/dozerdb:5.26.3.0`), `docker compose up -d --no-deps neo4j`,
  bolt `127.0.0.1:7687`, password in `.env` `NEO4J_PASSWORD`. (The `apoc-extended-init`
  sidecar fails with exit 23; `--no-deps` bypasses it — core apoc still auto-installs.)
- New code, all under `scripts/finbench/` + `examples/finbench/`:
  - `gen_duckdb.py` (B1) — deterministic generator + `gold.json`.
  - `load_to_graph.py` (B2) — Parquet → `{nodes,relationships}` → `GraphStore.write` (no LLM).
  - `verify_scenarios.py` (B3) — bounded-Cypher ground-truth check against the serving DB.
  - `finbench.ontology.yaml` (B4) — FIBO-style schema that arms the guardrail.
  - `cases.json` (B4) — five middleware-advantage scenarios with planted gold.
  - `mara_breakdown.py` (B7) — per-model Graph Agentic RAG breakdown (graph_cot).
- Data lands in `outputs/finbench/sf{N}/` (gitignored).

## Milestones

- [x] B1 — DuckDB generator (deterministic, planted patterns) + tests
- [x] B2 — typed Parquet→graph loader + tests
- [x] B3 — DozerDB load (SF1) + graph-level gold verification
- [x] B4 — FinBench ontology + five showcase scenarios
- [x] B7 — 3-model MARA breakdown (DeepSeek-V3.1, MiniMax-M2.5, gpt-oss-120b)
- [x] Channel dimension — the schema-cardinality scale axis (12 researched channels)
- [x] Fix relationship-aware aggregation (seocho-k2v) and re-measure
- [x] Fix list_all slot discard + ontology-aware display (seocho-pl1)
- [x] Direction guardrail (ontology-declared endpoints), with repair counting
- [x] S1–S5 stage-wise instrumentation (precision/superset, sargability, guardrail rate)
- [x] Bulk load path (neo4j-admin import) — unlocked SF100 / SF1000
- [x] B6 — scale curve SF1→SF1000, tuned vs naive plan shape
- [x] Measurement protocol: warm-up, repeats, p99, FDR disclosure, query timeout
- [x] Ontology ablation — schema grounding raises sargability 0% -> 75%
- [x] Sargable anchors across every anchored template (seocho-1dp)
- [x] Schema-reachability pruning + directed paths (seocho-z1q)
- [x] Promote plan-quality / slot / routing signals onto spans (seocho-d6x.5)
- [x] Measure template catalog vs validated generation on the same axes
- [x] Route between the two arms, env-gated (seocho-4bi)
- [x] Routing verified at SF1000 (89%, 369 db hits, 122 ms engine)
- [x] by_route reaches 9/9 at SF1000; route mix matches the policy
- [ ] Decide whether by_route becomes the default after multi-model measurement
- [x] Analyst-grade AML scenarios with cited typologies and real timelines
- [x] Relational arm (text2SQL over the same Parquet): 88% vs 25%
- [x] Infrastructure axis: page-cache sweep (6 GiB → 128 MiB) — no cliff
- [x] Infrastructure axis: cgroup memory sweep — no gradient, binary floor at ~4 GB
- [x] YAML-declared runs (`bench.yaml`) with parameters echoed beside results
- [x] Ranked synthesis: which axis mattered, and what infra should do about it
- [x] Power-law degree generation (`--hub-skew`); hub degree 31 → 158,315 at SF1000
- [x] Degree-band probe: guardrail shape flat, aggregates time out, truncation inverts
- [x] Memory/page-cache sweep against the hub graph with curated anchors — the
      degree-less "no effect" result is falsified but only mildly: 2.3x at 256 MiB for
      large neighbourhoods, flat for small ones, db hits constant throughout
- [x] Parameter curation by intermediate result size — 197x tighter than degree at the
      medium band; L2 predicts measured db hits at ~2x across five orders of magnitude
- [ ] Truncation the engine can actually serve: relationship index on `ts`, measured
      against the 70,000x pessimization the naive Cypher rewrite produced
- [x] Triadic closure and cyclic closure as separate generator knobs; clustering 0.000 →
      0.1415, incidental 3-rings 47 → 471,151 (lower bound)
- [x] Measured cardinality in the ontology (`degreeHint`), derived from the snapshot
- [ ] Pre-flight cost gate: predict db hits from L2 before execution, then bound,
      reformulate, approximate-and-say-so, or refuse. Two cases already fail without it —
      the hub-band aggregate (>30 s) and the unanchored ring question
- [ ] Driver overhead as a share of server time — decides whether a Rust/bolt layer is
      justified by enforcement (early-abort streaming, transaction bounds) rather than by
      latency, which is 0.3–0.9% of end to end
- [ ] Discriminating scenario: heterogeneous edge types along a variable-depth path
- [ ] SF10000 (~25 GiB store, fits this host) then SF100000 (~250 GiB, needs a large-memory instance)
- [ ] Optional: exercise the PG→LPG projection path instead of direct load

## Outcomes & Retrospective

### The thesis, proven directly: accuracy degrades with scale alone

Same model, same middleware, same nine questions, same code. Only the graph grew:

| gpt-oss-120b | SF1 | SF1000 |
|---|---|---|
| accuracy | **100%** (9/9) | **67%** (6/9) |

The three failures are precisely the non-sargable queries:

- `fan_in_smurfing`, `structuring_under_ctr` — 16,500,382 dbHits on a
  NodeByLabelScan, and the answer degrades from 25 to **0**.
- `laundering_cycle` — `TransactionTimedOut`; the 60 s budget turned the
  seocho-z1q hang (9m13s) into a clean failure rather than a stall.

The first two are **our own code**: the relationship-aware count added for
seocho-k2v anchors with `CONTAINS` over `coalesce(name, uri, id)`, which no index
can serve. It passed every SF1 test and is wrong at SF1000 (filed as seocho-1dp).
That is the sharpest available demonstration of the thesis — small-scale accuracy
cannot detect an unshippable query plan, not even your own.

### text2SQL beat text2Cypher 88% to 25% — and my prediction was wrong

Same eight AML questions, same source rows (DuckDB reads the Parquet the graph was
loaded from), same scoring over returned rows with synthesis excluded from both arms:

| arm | accuracy | partial | rejected | errors |
|---|---|---|---|---|
| SQL (DuckDB) | **88%** (7/8) | 0 | 0 | 0 |
| Cypher (DozerDB) | 25% (2/8) | 2 | 0 | 0 |

I predicted the traversal questions — the length-3 cycle and the 3-hop time-ordered
chain — would be answerable only in Cypher. **They were not.** SQL answered both, and
the generated queries show it did so legitimately rather than by hardcoding:

```sql
-- layered_chain_within_days
WITH RECURSIVE paths AS (
    SELECT src, dst, ts, 1 AS depth FROM transfer WHERE src = 9000200
    UNION ALL
    SELECT p.dst, t.dst, t.ts, p.depth + 1
    FROM paths p JOIN transfer t ON t.src = p.dst
    WHERE p.depth < 3 AND t.ts > p.ts       -- the monotonic-time condition the
)                                            -- Cypher arm never produced
```

So "these questions need a graph" was an assumption, and the data refuted it. **Fixed
depth is expressible as self-joins or a bounded recursive CTE**; nothing in these eight
scenarios required variable depth decided at query time.

Two honest qualifications, both of which matter more than the headline:

1. **This is not a paradigm result.** Ground-truth Cypher answers all of these (verified
   earlier at 100%), so 25% measures our Cypher generation, not the graph model. The
   comparison is closer to "SQL generation maturity vs our Cypher generation maturity" —
   and the literature predicted exactly that asymmetry, since LLMs have vastly more SQL
   training data. That confound was flagged before running and is now quantified.
2. **The scenario set does not discriminate.** All eight questions traverse a single
   homogeneous edge type (`transfer`), with the channel as a column. That is the case
   SQL handles well. The discriminating case — heterogeneous edge types along a
   variable-depth path — is untested, and is the next thing to build.

### Analyst-grade questions collapse to 25%

The nine original cases were single-fact lookups. Replacing them with eight questions
phrased the way an AML analyst asks — a window, a threshold, several conditions, and
more than one value to return — dropped accuracy from 100% to **25% (2/8)** on the same
graph and model:

| stage | value |
|---|---|
| S2 slot-fill | 50% |
| S3 supported | 50% |
| S4 sargable | 62% |
| **S5 accuracy** | **25%** |

Ground-truth Cypher answers all of them (verified: gold matched 100% on every
scenario), so this is a query-construction limit, not a data or schema limit. Four
distinct failure modes, each actionable:

1. **Multi-value answers.** `structuring_ctr_evasion` returned "25 accounts" and
   omitted the aggregate (249,675,000) — recall 0.50. The question asks for a count
   *and* a total.
2. **Intent misclassification.** `rapid_passthrough_velocity` and
   `aggregate_exceeds_threshold_none_individually` were routed to
   `financial_metric_lookup` — the phrase "how much" appears to trigger the finance
   template — producing 13,421-db-hit scans returning "No data".
3. **Two-stage queries.** `funnel_account_onward_wire` reached the generation arm and
   still returned nothing: "find the last inbound timestamp, then the outbound after
   it" needs a subquery the generator did not produce.
4. **Ordering along a path.** `layered_chain_within_days` could not express
   monotonically increasing timestamps across hops.

The two that succeeded (`layering_cycle_return`, `smurf_network_shared_beneficiary`)
are the ones expressible as a single traversal or a single grouped aggregate.

This is the more honest headline than 9/9: on questions that look like the job, the
gap between "answers a lookup" and "does analyst work" is 100% versus 25%.

### The degree distribution was missing, and it is the axis that actually fails

Everything above was measured on a graph whose edges were drawn by uniform random
attachment: `floor(random() * n_accounts)` for both endpoints. That produces a *binomial*
degree distribution, and at SF1000 it measured mean 10.00, p99 18, **max 31** — max/mean
of 3.1, no tail whatsoever.

That is not a detail, it is the omission of the thing LDBC FinBench exists to test.
FinBench's stated difference from the social-network benchmark is precisely hub vertices:
"the degree of hub vertices ... may scale up to millions in large data scales, which is
significantly higher than in social networks. The higher degree of hub vertices poses new
challenges to the performance of systems." Calling this dataset FinBench-style while
generating it uniformly removed the benchmark's reason to exist.

The generator now takes `--hub-skew`. Sampling ids as `n * random()**skew` makes
`P(id < m) = (m/N)**(1/skew)`, so expected degree by rank falls off as `r**(1/skew - 1)`
— a power law. At SF1000 with skew 3 the top account lands on **158,315** edges
(max/mean 15,677 against the same mean of 10). `skew = 1` emits the original SQL verbatim,
verified byte-identical, so earlier snapshots stay reproducible.

Same 2-hop question, anchors curated one per degree band, warm p50 over 3 repeats:

| band | anchor degree | `LIMIT 50` (what the guardrail emits) | `count(DISTINCT)` (no early exit) |
|---|---|---|---|
| median | 6 | 213 hits · 3.0 ms | 158,487 hits · 44.8 ms |
| p99 | 73 | 180 hits · 2.7 ms | 3,876 hits · 3.4 ms |
| p99.9 | 336 | 267 hits · 3.4 ms | 429,042 hits · 123.2 ms |
| **hub** | **158,315** | **163 hits · 2.8 ms** | **timeout (>30 s)** |

**My prediction was wrong, and the way it was wrong is the finding.** I expected the
indexed anchor to stop helping on a hub, because expanding a node costs O(degree). It
does not: the guardrail's shape is *flat* from degree 6 to degree 158,315 — 163–267 db
hits, 2.7–3.4 ms, index seek intact throughout. Cypher evaluates lazily, so
`DISTINCT ... LIMIT 50` stops as soon as 50 rows exist and never walks the hub's edge
list.

The collapse is real but it is gated on **question type, not data size**. A question that
cannot early-terminate — a count, a sum, a ranked top-K, an exhaustive path enumeration —
has no `LIMIT` to stop at, and on the hub it does not return at all. This is uncomfortable
for the guardrail: `max_result_rows` bounds the *result*, and that bound is load-bearing
only when an arbitrary subset is an acceptable answer. AML questions are mostly the other
kind. "How many counterparties did this account touch" cannot be answered with the first
50 it finds.

**Anchor degree does not predict cost.** The median-degree anchor (6) cost 158,487 db hits
while the p99 anchor (73) cost 3,876 — twelve times the degree, forty times cheaper, and
the sequence 6 → 73 → 336 → 158,315 maps to 158k → 3.9k → 429k → timeout, which is not
monotonic in any direction. Preferential attachment is why: edges are sampled in
proportion to degree, so a low-degree node's few neighbours are disproportionately likely
to *be* hubs. Cost follows the neighbourhood, not the anchor.

That result invalidates the curation scheme in the same breath as it motivates it. The
generator publishes one anchor per degree band, and this shows degree bands are the wrong
key. LDBC reached the same place first and solved it properly: parameter curation selects
bindings by *intermediate result size* at every level of the query plan, precisely so
runtimes are comparable despite skew (Gubichev & Boncz, TPCTC 2014). Curating by an
anchor-local property cannot work, and the honest status is that this experiment does not
yet curate parameters.

**FinBench's own mitigation, written as Cypher, costs more than the problem.** FinBench
handles hubs with truncation — `TRUNCATION_LIMIT` and `TRUNCATION_ORDER` (default
`TIMESTAMP_DESCENDING`) restrict a traversal to the K most recent edges, described in the
spec as "a deterministic sampling in traversing". Expressed the obvious way, as
`ORDER BY r.ts DESC LIMIT 5000` per hop, it costs **11,502,593 db hits and 2,723 ms** on
the hub — against 163 db hits and 2.8 ms for doing nothing at all. A **70,000x
pessimization** from the mitigation.

The reason is that `ORDER BY` is a pipeline breaker: to return the 5,000 most recent
edges the engine materialises and sorts all 158,315 first, discarding the laziness that
was doing the actual protecting. Truncation is only cheap if the *engine* offers ordered
top-K expansion — an index on the relationship's ordering property, consumed during the
expand. This is a clean infrastructure-axis result: no amount of middleware cleverness
recovers an operation the engine does not provide, and here the defence costs four orders
of magnitude more than the attack.

*Two qualifications.* The two arms do **not** return the same answer — `LIMIT 50` yields
an arbitrary 50, `truncated` yields 50 drawn from the 5,000 most recent — so unlike the
tuned/naive comparison, which was answer-identical and therefore a pure cost measurement,
this one compares cost across different semantics. And bulk load of the hub graph ran at
444k rel/s against 673k for the uniform graph at identical size, so degree skew taxes the
load path by about a third as well.

### Structural properties: what uniform sampling silently removed

Three separate conclusions in this plan turned out to be scoped to an unstated property of
the generated data. The pattern is identical each time, so it is worth naming: **scale was
a parameter and shape was not**, so a thousand-fold increase in volume never once changed
the structure the queries traversed.

`graph_properties.py` measures all of it and merges the result into the snapshot manifest —
the same role FinBench's "factor tables" play. Measured at SF1000:

| property | uniform | power-law (`--hub-skew 3`) | real networks |
|---|---|---|---|
| max degree | 31 | **158,315** | hubs to millions (FinBench) |
| redundancy (edges / distinct pairs) | 1.0000x | 1.0031x | multiplicity modelled |
| max edge multiplicity | **2** | 607 | repeated counterparty pairs are routine |
| avg local clustering | **0.000** | 6.5e-3 | **0.1 – 0.5** |
| sampled nodes in any triangle | **0 of 2,997** | 346 of 2,885 (12.0%) | most |
| incidental directed 3-cycles | 47 | 88 | orders of magnitude more |

Each absence disables a specific measurement rather than merely reducing realism.

**No multiplicity means semantic errors are unscoreable.** At redundancy 1.0000x,
`count(dst)` and `count(DISTINCT dst)` return the *same number*, so "how many
counterparties" and "how many transfers" are indistinguishable and an agent that confuses
them scores correct. The profile reports this directly as
`distinct_vs_total_distinguishable`, which is **false on both graphs** — 1.0031x is not
enough either. Three of the four hub-anchored cases have `fan_out` exactly equal to `L1`,
so they cannot detect the error class either.

**No triadic closure means motif detection is untested.** Zero triangles across a
3,000-node sample, and 47 incidental directed 3-cycles in a 10M-edge graph. The
`laundering_cycle` scenario asks "starting from account 9000001, is there a 3-cycle" —
*anchored on a known account*, so other cycles never enter the query. It measures recall and
receives precision for free. The question an analyst actually has — find the suspicious
rings among the ordinary ones — was never posed, and could not be posed here: a detection
task without distractors does not test detection. This is the sharpest of the three gaps
because it invalidates a scenario's *claim* rather than a measurement's *scope*.

**No hubs meant the memory conclusions were mechanism-bound**, which is covered in the
degree and memory sections above.

Degree and multiplicity are now generator parameters. Clustering is **not**: producing
triadic closure needs a generation model that closes triangles deliberately (an
attachment rule that prefers a neighbour's neighbour), not a sampling tweak, so it stays
measured-but-uncontrolled and is recorded here as the honest status rather than quietly
omitted.

*Measurement bounds, reported because they are not exact.* Local clustering is sampled over
nodes of degree 2–200: the neighbour-pair join is quadratic in degree and dies on a hub, so
hubs are excluded from the *sample*, not the graph — which is the region a motif traversal
walks anyway. Directed 3-cycles are a **lower bound** over nodes below an out-degree cap of
40, covering 99.3% of edges on the uniform graph and 60.9% on the power-law one; the
unbounded 2-path join exhausted a 10 GB limit and was OOM-killed.

### Directional roles: the ontology's vocabulary decides what the prompt can say

Hub-anchored cases scored **1/12** with the full ontology. The cause was not the model and
not the prompt wording. Every generated count came out as an in-degree:

```cypher
MATCH (src:Account)-[:TRANSFER]->(anchor:Account)   -- question: what did anchor send TO
RETURN count(DISTINCT src)
```

Two things had to be true at once. `_count` hardcoded the anchor as the arrow's *target* —
correct for `(Company)-[:HAS_METRIC]->(Metric)`, wrong for every outgoing question. And
`_orient_relationship`, the guardrail that repairs reversed endpoints, returns early when
`source == target` because a label comparison has nothing to say there. `TRANSFER:
Account -> Account` hits that branch, so the guardrail reported no repair while emitting the
reverse of what was asked.

**The ontology could express endpoint types but not directional roles.** With both ends
labelled `Account`, "which accounts did X pay" and "which accounts paid X" are the same
query to the schema. `RelDef` now carries `source_role` / `target_role`, and FinBench
declares `sender` / `beneficiary`.

Results on the hub-anchored set, ablated against a labels-only ontology:

| | full ontology | labels only |
|---|---|---|
| accuracy | 1/12 → **12/12 (100%)** | **0/12 (0%)** |
| sargable | 100% | 0% |
| db hits | 1,102,545 → **537,477** | **64,001,516** (119x) |
| engine total | 458 ms → 218 ms | 6,055 ms |

The labels-only arm answers **2,000,047** — the total account count — to every fan-out
question and **0** to every two-hop. Without declared relationships, retrieval does not
degrade, it collapses to the whole graph. That gap is the ontology's contribution to
retrieval measured directly, and it is the whole result rather than a margin.

**The first version of this fix was overfitted, and the check caught it.** Direction was
resolved by substring-matching the question against a hand-authored phrase list. It scored
12/12 — on questions whose phrasings the same author had written into the list. Tried on
paraphrases meaning the same thing ("who did X pay", "which accounts funded X", "list the
beneficiaries of X"), it scored **0 of 6**, and a miss returns `""`, which falls back to the
old anchor-as-target assumption: a silent reinstatement of the bug.

So the phrase list was demoted to a deterministic fast path and the general case moved to
where it belongs. The intent-extraction contract now asks for `anchor_role`, grounded on
the role names the ontology declares, and only for relationships that declare them:

```
Directional roles (both endpoints may share a label, so the arrow — not the
label — carries the direction):
  - TRANSFER: the tail of the arrow is the sender, the head is the beneficiary

"anchor_role": "source" if the anchor is the tail of the arrow (it acts),
               "target" if the anchor is the head, empty if symmetric
```

Re-measured with three held-out wordings added to the case set — `pay`,
`beneficiaries … send funds to`, `push funds toward`, none of them in the declared list —
the full arm scores **3/3 on the paraphrases** and 12/12 overall, while the labels-only arm
answers 2,000,047 to all three.

**This is the precise shape of the "ontology drives retrieval" claim.** The improvement did
not come from better prompt phrasing; it came from the ontology gaining a vocabulary it did
not have, after which the prompt could carry it. A prompt can only convey what the schema
holds — which is also why hardcoding phrases was the wrong fix: it was the author supplying
answers, not the ontology supplying vocabulary. The same argument applies to the
cardinality gap that remains: nothing in the ontology tells a model that one account has
158,315 edges and the median has 6, so nothing in the prompt can either.

### Detection: precision collapses with scale, recall does not move

Every question in this experiment until now named its own anchor — "account 18503 is under
review" — which hands the detector its answer and makes precision automatic. That measured
recall and called it detection. Six planted patterns, asked the way an analyst starts (no
account named), across SF1/10/100 and both distributions:

| pattern | SF1 | SF10 | SF100 | recall |
|---|---|---|---|---|
| loan integration · uniform | 0.0013 (753) | 0.0001 (7,541) | **0.0000 (74,832)** | 100% |
| loan integration · power-law | 0.0009 (1,163) | 0.0001 (11,195) | **0.0000 (112,470)** | 100% |
| nominee structuring · power-law | 1.0000 (1) | 1.0000 (1) | **0.1429 (7)** | 100% |
| equity integration · uniform | 0.5000 | 0.5000 | 0.5000 | 100% |
| common control · power-law | 0.2500 (4) | 0.5000 (2) | **1.0000 (1)** | 100% |
| layering cycle | 1.0000 | 1.0000 | 1.0000 | 100% |

**Recall was 100% at all 36 measurements.** The planted pattern is still there and still
findable at every scale. What degrades is whether the finding is usable: `loan_integration`
returns candidates in proportion to the graph — 753 to 74,832, a hundredfold — for one true
positive throughout. At SF100 an analyst receives 112,470 candidates and one of them is real.

That is the scalability claim in the terms that matter to the person doing the work, and it is
invisible to every metric this experiment tracked before: accuracy, db hits and latency are all
fine here. The rule runs, returns, and is correct in the sense of containing the answer.

**Power-law is consistently worse at identical edge counts** — 1,163 against 753, 11,195
against 7,541, 112,470 against 74,832 — so distribution reaches detection the same way it
reached cost.

**Nominee structuring is where the user's concern lands.** It holds precision 1.0 to SF10 and
falls to 0.1429 at SF100 on the power-law graph: as the graph grows, innocent owners start
satisfying "many small legs across several accounts" by coincidence. That is the practical
version of "a nominee ring is hard to find when accounts are many and amounts are mixed".

**One rule improves with scale, and it is the multi-layer one.** `common_control` goes 4 → 2 →
1 candidate, because a conjunction across the account, party and device layers becomes *more*
selective as the graph grows — innocent triples rarely satisfy all three at once. That is the
measured payoff of completing the schema to all nine FinBench edge types: layered conjunctions
resist the precision collapse that single-layer rules suffer.

#### Two data defects had to be fixed before any of this meant anything

**Amount was doing all the work.** Planted transfers ran to ~10M against an innocent ceiling of
50,010, so `amount > 1000000` isolated every planted edge with **100% precision**. Any detection
score would have measured that giveaway rather than a detector. Fixed by giving ordinary traffic
a heavy amount tail — real transfer amounts have one — which dropped the filter's precision to
2.7–5.6% and made the two populations overlap. Fixing it in the plants instead would have been
wrong: a funnel wiring its collected total *is* a large amount, and shrinking it would trade one
unrealism for another.

**Ranking by size misses the worst case.** The nominee ring aggregates 12M across 468 small legs
while innocent owners reach hundreds of millions on a handful of large legitimate transfers.
Sorted by total, the planted owner sat **100th of 103** owners above the threshold. So the rule
keys on structure — accounts per owner, legs per account, spread over time — not magnitude.

Removing the giveaway also removed the property it had been standing in for: at 25 senders with
ordinary amounts the fan-in aggregate came to 599,350, a sixteenth of the threshold, so the
typology's defining "each unreportable, the aggregate reportable" was simply absent. The two had
to be repaired together, which is why the sender count is now 420.

*Rules are the plausible first attempt, not tuned oracles.* The measurement is how a reasonable
rule degrades, so a low score is a result. Cycle counts are a lower bound over nodes below an
out-degree cap, because the unbounded two-path join exhausts 10 GB here.

### Triadic closure, and why clustering and cycles are two knobs

Clustering measured **exactly 0.000** on both the uniform and the power-law graph — zero
triangles across a 3,000-node sample — against 0.1-0.5 in real payment networks. The
generator now closes two-paths, and the first attempt at it was subtly wrong in a way the
measurement forced out.

`--closure-share` completes `a->b->c` with `a->c`. That is triadic closure in the
undirected sense and it worked: clustering went 0.000 to **0.175** at SF1, with 77% of
sampled nodes sitting in a triangle. But directed 3-cycles barely moved (47 to 110),
because a transitive triangle is *not* a cycle — a cycle needs `c->a`. A laundering ring
is a directed cycle, so the scenario that motivated this work would still have had no
distractors while the clustering number looked healthy. Two structural properties, two
scenario families; conflating them hides exactly the case you were trying to create.

`--cycle-share` closes into cycles instead, using the laundering channels so the
distractor rings are plausible rather than arbitrary. At SF1000 with
`--hub-skew 3 --closure-share 0.10 --cycle-share 0.15`:

| property | uniform | power-law only | + closure + cycles |
|---|---|---|---|
| avg local clustering | 0.000 | 6.5e-3 | **0.1415** |
| sampled nodes in a triangle | 0 of 2,997 | 12.0% | **82.3%** |
| directed 3-cycles | 47 | 88 | **694,682** |
| max edge multiplicity | 2 | 607 | 450 |

Defaults are unchanged and verified byte-identical, so every earlier snapshot still
reproduces.

**What this buys is a measurement that could not previously be taken.** The planted ring
(9000001 -> 9000002 -> 9000003) survives intact, and it now sits among **471,151**
incidental 3-rings — counted only among accounts of degree below 40, so a lower bound.
Against 47 in the whole uniform graph. The existing `laundering_cycle` case asks "starting
from account 9000001, is there a 3-cycle", anchored, so no other ring ever entered the
query: it measured recall and received precision free. The unanchored question an analyst
actually asks — *which* rings here are suspicious — is now answerable and now hard, which
is the point.

That question is also the first case with no anchor at all: no index seek, no
early-terminable shape, and an answer set of half a million candidates. It is the natural
first test of a pre-flight cost gate, because the correct output is not a query but a
judgement that the question cannot be answered exhaustively within budget.

### Cardinality in the ontology: the same argument, one level along

Directional roles established that a prompt can only convey what the schema holds. The
identical gap sat next to it: nothing in a schema of labels, property names and endpoint
types says that one account carries tens of thousands of transfer edges while the median
carries three. So a planner had no basis for preferring a bounded shape, and the measured
consequence is an aggregate that does not return at all where the same question on a median
node costs 45 ms.

`RelDef.degree_hint` now carries measured degree facts, and
`annotate_ontology_degrees.py` *derives* them from the snapshot rather than asserting them
— recording `measured_from`, and staying silent for relationships whose degree carries no
information (one OWN per account, one DEPOSIT per loan). The `heavy_tailed` threshold of 50x
comes from the measured contrast, not from taste: the uniform graph sits at max/median ~3
and shows no hub effect, the power-law graph at ~25,700 and times out. Both prompts now
carry it:

```
Degree distribution (measured on the loaded data):
  - TRANSFER: median out-degree 3, p99 41, maximum 77,036 — a few nodes carry
    orders of magnitude more edges than the typical one
```

Whether that changes behaviour is not yet measured, and the honest expectation is limited:
the model cannot make `count(DISTINCT)` cheap on a hub. What the fact enables is a
*decision* — route, bound, approximate, or refuse — which belongs in the planner rather
than in generated Cypher. The 70,000x truncation result is the constraint: FinBench's own
mitigation, written as user-level Cypher, cost four orders of magnitude more than no
mitigation at all, so supernode awareness cannot mean "emit a cleverer query".

### Parameter curation: anchors chosen by cost, and the degree baseline it replaces

The degree probe left the experiment unable to measure anything else on the hub graph.
If picking an anchor can swing cost forty-fold in either direction, an infrastructure
sweep cannot distinguish "this memory profile is slower" from "this anchor was harder" —
the anchor becomes an uncontrolled variable larger than the effect being measured.

LDBC's answer is parameter curation: choose bindings whose *intermediate result sizes*
are similar at each level of the intended plan (Gubichev & Boncz, TPCTC 2014).
`curate_parameters.py` does that for the 2-hop expansion, computed offline in DuckDB
against the Parquet snapshot so the selection never depends on the engine whose cost it
is meant to control for:

    L1 = |{b : a -> b}|                       first expansion
    L2 = sum over those edges of outdeg(b)    second expansion — the real cost driver

Anchors are then grouped into bands by L2 quantile and, within a band, the five closest
to the band target are taken. The comparison arm keys on L1 (anchor degree) instead,
using identical bands and identical selection logic, so the two are measured rather than
argued about.

Same query, same graph, five anchors per band, measured db hits:

| band | curated by **L2** | | keyed on **degree** | |
|---|---|---|---|---|
| | mean hits | **cv** | mean hits | **cv** |
| tiny | 51 | 0.2144 | 42 | 0.1277 |
| small | 300 | 0.0740 | 735 | 0.9963 |
| medium | 7,317 | **0.0097** | 67,224 | **1.9068** |
| large | 162,997 | **0.0003** | 85,785 | 0.7449 |
| huge | 382,596 | 0.0015 | 306,909 | 0.2746 |

At the `medium` band, curating on L2 is **197x tighter** than keying on degree; at
`large`, 2,483x. And the degree arm's failure is sharper than "less tight": its degree cv
is exactly **0.0** — every anchor in a band has *identical* degree — while measured cost
varies with a cv of 1.91, meaning the standard deviation is nearly twice the mean. Holding
the anchor's own degree perfectly constant does not constrain its cost at all.

The offline estimate turns out to predict engine cost almost exactly: measured db hits
land at **≈ 2 x L2** across five orders of magnitude (5 → 51, 3,545 → 7,317, 81,455 →
162,997, 190,788 → 382,596). Curation therefore needs no database round trips, which
matters because the anchors have to be fixed *before* a sweep starts, not discovered
during it.

Two honest limits. The `tiny` band's cv of 0.21 is fixed per-query overhead dominating
50-odd db hits, not a curation failure — the metric stops being meaningful when the
signal is that small. And this curates one query template's plan; a different template
has different levels and needs its own estimator, which is exactly the manual step LDBC
automates and we do not.

### Infrastructure axis, part 3: hubs make memory matter — a little

The two earlier memory sweeps concluded that memory does not matter, and both offered the
same mechanism: the query's working set was about 25 pages, the same 25 pages every
repetition, so they stayed resident no matter how small the cache. That mechanism has a
boundary, and both sweeps ran on the wrong side of it — a 25-page working set is a
property of a graph with no hubs, not of the engine or the plan.

Re-run against the power-law graph with anchors from `curate_parameters.py`, using the
aggregate shape (a `LIMIT`-able question early-terminates and was already shown flat from
degree 6 to 158,315, so it cannot reveal a memory effect). Warm p50, three curated anchors
per band, five repeats each:

| container mem | heap | cache | small (306 hits) | medium (7,358) | large (163,027) | huge (382,626) |
|---|---|---|---|---|---|---|
| unlimited | 8G | 6G | 4.2 ms | 5.7 ms | 40.1 ms | 87.2 ms |
| 6g | 3G | 1G | 4.7 ms (1.12x) | 5.7 ms (1.00x) | 41.8 ms (1.04x) | 98.3 ms (1.13x) |
| 4g | 2G | 1G | 5.3 ms (1.28x) | 10.1 ms (1.75x) | 46.1 ms (1.15x) | 111.3 ms (1.28x) |
| 4g | 2G | 256M | 5.2 ms (1.26x) | 6.8 ms (1.18x) | **93.6 ms (2.34x)** | **200.2 ms (2.30x)** |

Db hits were *identical* across all four profiles (305.7 / 7,357.7 / 163,027.3 /
382,626.3), which is the control: the plan did not change when memory did, so every
latency movement is physical rather than a replan.

**The earlier conclusion is falsified, and the correction is small.** On the degree-less
graph, cutting the cache from 6 GiB to 128 MiB moved warm p50 from 2.2 ms to 2.1 ms —
nothing. Here, cutting 6 GiB to 256 MiB costs **2.3x**. Memory does register once there
are hubs. But 2.3x from a 24x cache reduction is a shallow curve with a bounded tail
(worst p99 229.9 ms), so memory moves from "no effect" to "small effect" and stays below
every software axis in the ranking. Plan shape was 264,005x.

**The effect is selective by working set, which confirms the mechanism rather than
replacing it.** `small` and `medium` barely move (1.18–1.28x) while `large` and `huge`
move 2.3x. Pressure appears exactly where the neighbourhood is too big to stay resident,
which is what the 25-page explanation predicted would happen if the working set ever grew.

**This time it really is the page cache.** `4g/2G/1G` gives `large` 1.15x while
`4g/2G/256M` gives 2.34x at the same container limit, so the variable is Neo4j's own cache
— unlike the uniform-graph sweep, where cache alone did nothing and only a cgroup limit
registered at all. The difference is that 383k db hits of neighbourhood genuinely exceeds
256 MiB, and 25 pages never exceeded anything.

The sharper way to say all of it: **hubs make memory matter a little and make question
type matter without bound.** The same hub anchor that costs 200 ms as a `LIMIT` query does
not return at all as an aggregate, and no amount of RAM changes that — it is a property of
having to walk the whole neighbourhood, which is a query-shape problem wearing an
infrastructure costume.

*Measurement noise:* `4g/2G/1G` shows `medium` at 1.75x (5.7 → 10.1 ms), off the otherwise
monotonic pattern. At single-digit milliseconds this run-to-run variation exceeds the
signal, so the small bands should be read as flat rather than as a trend.

### Infrastructure axis, part 2: the failure is a wall, not a slope

Capping container memory with a cgroup limit caps the OS page cache too, which the
page-cache-only sweep could not do. Against the same 2.87 GB store:

| container mem | heap | cache | tuned p50 | tuned p99 | tuned **cold** | naive p50 |
|---|---|---|---|---|---|---|
| unlimited | 8G | 6G | 1.8–2.8 ms | 5.0–6.0 ms | 23.7–33.1 ms | 745–1035 ms |
| 6 GB | 3G | 1G | 2.4–2.9 ms | 5.7–7.6 ms | **36–93 ms** | 984 ms |
| 4 GB | 2G | 1G | 1.9–2.8 ms | 5.4–8.2 ms | **38–107 ms** | 961 ms |
| 2 GB | 1G | 512M | — | — | — | **database never came online** |

Three findings:

1. **Still no cliff in warm latency.** Squeezing the container to 1.4x the store size left
   tuned p50 at 1.9–2.9 ms. Same conclusion as the first sweep, now with the OS cache
   genuinely constrained.
2. **Pressure shows up in cold, not warm.** Cold went 23.7–33.1 ms (unconstrained) to
   36–107 ms while warm stayed flat, so the cold/warm ratio widened from 10–16x to ~32x.
   When the working set is 25 pages, memory pressure is paid once as warm-up cost and then
   disappears. That is *why* the tuned plan is scale-insensitive — a stronger explanation
   than db-hit counting alone.
3. **The real limit is a startup wall.** At `mem=2g, heap=1G` the database does not come
   online at all. So the operational answer for serving SF1000 is not "performance
   degrades below N GB" but "it works at 4 GB and does not start at 2 GB" — a threshold to
   provision above, not a curve to interpolate.

For an infrastructure owner this inverts the usual intuition: there is no gradual
degradation to monitor on the query path, and the thing to size for is the floor at which
the process starts.

*Measurement note:* the `unlimited/8G/6G` baseline row first failed as
`database_not_online` — the container had just been recreated and WAL replay exceeded the
180 s poll budget. Re-measured separately once the database was online (same config,
verified live: `pagecache 6.00GiB`, `heap 8.00GiB`, `mem_limit 0`), it produced the numbers
above, confirming a harness timeout rather than a configuration result. Worth keeping as a
harness lesson: a restart-per-profile sweep will occasionally report a config as broken
when the poll budget, not the config, ran out — which is exactly the shape of a false
infrastructure conclusion.

### Infrastructure axis: page cache alone cannot create a cliff

The scale curve showed a label-anchored plan holds 25 db hits at every scale factor. Db
hits are a *logical* unit though — 25 served from cache and 25 served from disk differ by
orders of magnitude — so constant work is not yet constant latency. The scale-up question
is where that translation breaks.

Testing it needs no hardware: a 2.87 GB store against a 512 MB cache exercises the same
eviction behaviour as a 27 GB store against a 5 GB cache. Shrinking the cache answers
"do we need a bigger instance?" by making the current one smaller.

Sweeping Neo4j's page cache from 6 GiB down to 128 MiB — 4.5% of the store:

| pagecache | tuned p50 | tuned p99 | naive p50 | tuned db hits |
|---|---|---|---|---|
| 6.00 GiB | 2.2–2.9 ms | 5.0–7.9 ms | 713–981 ms | 25 / 12 / 53 |
| 2.00 GiB | 2.4–3.3 ms | 4.9–8.3 ms | 712–973 ms | 25 / 12 / 53 |
| 512 MiB | 2.1–3.1 ms | 5.2–8.2 ms | 721–1069 ms | 25 / 12 / 53 |
| 128 MiB | 2.1–3.3 ms | 4.7–7.9 ms | 778–1065 ms | 25 / 12 / 53 |

**No cliff, and db hits never moved** (the latter confirming measurement integrity). A
47x cache reduction changed tuned p50 from 2.2 ms to 2.1 ms — noise.

The mechanism is worth stating because it generalises: an index seek touches ~25 pages,
those same pages are touched on every repeat, so they stay hot in any cache large enough
to hold 25 pages. **When the working set is small, cache size is irrelevant** — which is
the real reason the tuned plan is scale-insensitive, beyond db-hit counting.

*Honest limit of this result.* The container had no memory limit, so the host's 62 GiB of
OS page cache held the store files and a Neo4j-level miss was still served from RAM. This
sweep therefore varied the internal cache while every miss landed in OS cache — it shows
**"page-cache configuration alone cannot produce a cliff"**, not "there is no cliff". A
cgroup `mem_limit` is needed to cap the OS cache too, which is the follow-up.

### Final: the policy book gets it to 9/9

Folding validated generation into `ROUTE_CATALOG` (rather than a process-wide flag)
reached 100% at SF1000 — 3.3M nodes / 19.6M relationships:

| run | accuracy | sargable | dbHits | engine ms |
|---|---|---|---|---|
| templates (rescued by repair) | 89% | 75% | 33,000,855 | 94,408 |
| templates fixed (repair suppressed) | 56% | 100% | 456 | 64,225 |
| `generated_first` (global switch) | 89% | 100% | 369 | 122 |
| **`by_route` (policy book)** | **100% (9/9)** | **100%** | **490** | 31,357 |

The route mix is what the policy intended: two questions took validated generation
(`nhop_neighborhood`, `laundering_cycle` — the variable-length hop and the cycle the
catalog cannot express) and seven stayed on the deterministic pass. Escalation costs a
model round trip, so confining it to the routes that need it beat applying it
everywhere: `generated_first` reached only 89% at similar plan cost.

The classifier needed widening alongside the catalog — `_MULTI_HOP_RE` matched
compositional wording but not traversal depth, so "reachable within 3 transfer hops"
classified as `entity_summary` and would have been routed to the very templates that
cannot express it.

### Three plausible numbers, three wiring bugs, and what caught them

Every measurement in this section was wrong at least once, and accuracy never revealed it:

| reported | actually | what exposed it |
|---|---|---|
| generated arm 0% (9/9 rejected) | my policy used `workspace_id` while SEOCHO writes `_workspace_id`; the prompt never stated the tenant convention | the rejection strings |
| `generated_first` 67% | `ask()` built the deterministic planner directly, bypassing the flag | `s1_intent` — no `generated` value anywhere |
| `by_route` 78% | `_build_planner` tested `!= generated_first`, so by_route fell back to templates | `engine route='template'` with `rejection=None` |

Each was a believable figure that supported a false conclusion ("generation cannot do
this", "routing barely helps", "the policy integration is pointless"). This is the
experiment's own thesis turned on the experiment: a plausible number is the dangerous
case, and only instrumentation separates it from a real one.

### Routing restored accuracy at a fraction of the cost

With `SEOCHO_QUERY_PRECEDENCE=generated_first` actually reaching the planner, SF1000:

| run | accuracy | sargable | dbHits | engine ms |
|---|---|---|---|---|
| templates (rescued by repair) | 89% | 75% | 33,000,855 | 94,408 |
| templates fixed (repair suppressed) | 56% | 100% | 456 | 64,225 |
| **routed** | **89% (8/9)** | **100%** | **369** | **122** |

The same 89%, reached very differently: **89,433x fewer db hits** and **774x less
engine time**, with `intent=generated` on all nine questions. Every question the
templates could not express — variable-length hops, cycle detection, channel
projection — is now answered.

`s2_slot_fill=0%` is the expected reading, not a fault: the generated arm fills no
template slots, so that metric does not apply to it. It is corroborating evidence
that routing engaged.

One failure remains (`channel_of_transfer`), which both arms answered correctly in
the arm comparison — run-to-run variance rather than a structural limit.

**The measurement that was wrong, and how we knew.** The first generated_first run
reported 67% and the flag had had no effect: `ask()` constructed the deterministic
planner directly, bypassing `_build_planner`. Accuracy alone would have supported the
false conclusion that routing barely helps. What exposed it was `s1_intent` — every
value was a template intent and none was `generated`. The experiment's own thesis,
applied to the experiment: a plausible number is the dangerous case, and only
instrumentation separates it from a real one.

### Template catalog vs validated generation — the arms are complementary

SEOCHO assembles Cypher from intent + ontology on purpose (ADR-0097's pattern
catalog, with per-pattern cost hints). What SF1000 exposed is a *coverage* gap, not a
wrong architecture: the nine patterns target entity lookup, one-hop relationships and
financial metrics, while these AML questions also need variable-length hops,
edge-property filters and edge-property projection. Outside that set the template
still answers — with a plausible row count and the wrong content.

SEOCHO already contained the complement: `generate_validated_cypher`
(query/text2cypher.py) refuses undeclared labels/relationships and unbounded paths,
forces tenant scope and LIMIT, and EXPLAINs before executing. It was referenced only
in `__all__` — written and never wired in.

Measured on the same questions, ontology and scoring (rows only; synthesis excluded):

| arm | accuracy | rejected | sargable | dbHits total |
|---|---|---|---|---|
| template | 67% (6/9) | 0 | 88% | 43,235,371 |
| generated | **78%** (7/9) | 2 | **100%** | **265** |

Generation covered exactly the gap — `nhop_neighborhood`
(`MATCH (start)-[:TRANSFER*1..3]->`) and `laundering_cycle`, which the templates
answer with rows=2 / rows=0 — and its plans were consistently cheaper
(flagged_lookup 20 → 5 db hits), the same plan-shaping effect the ablation showed.
But `high_risk_channel_hops` was answered only by the template. So the fix is a
routing order, not a replacement: `SEOCHO_QUERY_PRECEDENCE=generated_first` consults
validated generation and falls back to the catalog, with template_first remaining the
default.

The two rejections were fail-closed, not wrong answers — the JSON was truncated at
`max_tokens=700`, since raised to 2000.

*Honesty note:* the first run of this harness reported generation at 0% (9/9
rejected). That measured configuration, not the arm — the policy defaults
`workspace_property` to `workspace_id` while SEOCHO writes `_workspace_id`, and the
prompt never stated the scoping convention, so the model invented a `Workspace` node
and the guardrail correctly refused it.

### A component-local fix regressed the system

Making the templates return rows suppressed the repair loop that had been rescuing
those questions: `direct_transfer` went from `reasoning_attempts=2` and a correct
answer to `reasoning_attempts=0` and a wrong one, and SF1000 accuracy fell 89% → 56%
*while* plan quality improved (sargable 75% → 100%, total db hits 33,000,855 → 456).
The repair trigger read "no rows" as "nothing to fix". It now also treats a single
row on a set-valued intent as inconclusive.

Worth stating plainly: three of the four remaining failures returned `rows=2` — a
plausible count with wrong content. Plausible-but-wrong is the failure mode that
neither accuracy nor a zero-row trigger can see.

### Ontology ablation: schema grounding is a plan-shaping prior

SF1000, identical graph/model/questions, varying only the schema given to the agent:

| arm | S2 slot-fill | S3 supported | **S4 sargable** | S5 accuracy |
|---|---|---|---|---|
| full (labels + relationships + properties) | 100% | 89% | **75%** | 67% |
| minimal (labels only) | 54% | 22% | **0%** | 0% |

The full arm resolves 6 of 9 scenarios through an index seek; the minimal arm
produces a full scan on **every** question and zero seeks. So the ontology does
not merely prevent label hallucination — it steers the model toward index-usable
shapes.

*Honest caveat:* the two effects cannot be fully separated here, because the
minimal arm produced no correct query at all, so "0% sargable" is partly "never
wrote a working query". Safe statement: labels alone yielded zero seeks and zero
correct answers; the full ontology yielded 75% seeks and 67% correct.

### Headline: plan shape, not data size, decides whether this scales

Each scenario run in two shapes against the same graph — `tuned` (label-qualified
with an indexed property) vs `naive` (unlabeled match, so the `:Account(id)` index
cannot be used and the planner falls back to AllNodesScan):

| dbHits | SF1 | SF10 | SF100 | SF1000 |
|---|---|---|---|---|
| tuned | 25 | 25 | 25 | **25** (constant) |
| naive | 6,722 | 66,122 | 660,122 | **6,600,122** (linear) |
| cost multiple | 269x | 2,645x | 26,405x | **264,005x** |

Under the corrected protocol (warm-up + 10 repetitions, warm p50), tuned latency is
flat across the whole range — **4.3 ms at SF1, 1.8 ms at SF1000** — while naive
tracks its dbHits: 17 ms → **814 ms**. A **411x** latency gap at SF1000, with p99
tight on both sides (5.2 ms vs 817.9 ms). **Both shapes return identical answers**,
so accuracy cannot tell them apart.

This inverts an earlier reading in this plan: SF1→SF10 looked sub-linear (1.7x for
10x data), but that was small-scale fixed cost masking a linear query. Extrapolating
scalability from SF1/SF10 was simply wrong.

**Benchmark-design consequence:** at SF1 the latency gap is 4.3 ms vs 17 ms while
dbHits already differ 269x — and unwarmed it read as 20 ms vs 19 ms, i.e. nothing. *dbHits is the leading indicator; small-scale
latency is not.* A benchmark reporting only accuracy and latency at low scale
factors cannot see this failure at all.

### Load path: the constraint was software, not hardware

| | SF10 (196k rels) | SF100 (1.96M) | SF1000 (19.6M) |
|---|---|---|---|
| transactional (bolt) | 874 s | ~3.8 h (proj.) | ~38 h (proj.) |
| neo4j-admin import | **1.6 s** | **5.6 s** | **29 s** |
| throughput | 121k rel/s | 348k rel/s | **673k rel/s** |

SF1000 = 3.3M nodes / 19.6M relationships / 2.5 GB, loaded in 36 s end-to-end on a
16-core / 62 GB workstation. The earlier "we may need a 128-core, 512 GB instance"
question resolved to "no": the bottleneck was bolt round-trips. Hardware sizing
should start after the software path is fixed. Extrapolating from the measured
SF1000 store (2.45 GiB): SF10000 ≈ 25 GiB, which still fits this host's 62 GiB;
RAM becomes the binding constraint around SF25000 (~62 GiB) and a large-memory
instance is only needed near SF100000 (~250 GiB).

### Where end-to-end latency actually goes

Instrumented run (gpt-oss-120b, SF1, 9 scenarios): **LLM 19,504 ms vs graph engine
184 ms** — the graph is **0.9%** of end-to-end latency. For agent workloads the
database is not the latency problem; the model is.

### Per-model breakdown, and what accuracy hides

| model | accuracy | S2 slot-fill | S4 sargable | S5 exact | superset |
|---|---|---|---|---|---|
| gpt-oss-120b | 100% (9/9) | 100% | **56%** | 78% | 33% |

100% accuracy concealed that **44% of its queries full-scan** and **a third of its
list answers over-answer**. At SF1 that is invisible; at SF1000 the same 44% costs
264,000x more db hits.

Across models (SF1, 9 scenarios, after the middleware fixes): DeepSeek-V3.1 89%,
MiniMax-M2.5 89%, gpt-oss-120b 100%. Track 7's thesis holds — the graph and
ontology never changed, only the LLM — but see the latency caveat below.

### Early SF1 result (superseded, kept for the record)

SF1 graph loaded transactionally in ~20 s; the three planted scenarios verified at
the graph level (cycle ~11 ms, fan-in ~31 ms). Those latencies were measured with
the naive plan shape and are not comparable to the tuned figures above.

The first five-scenario run scored all three models at 80% (4/5) with p50 spanning
3,358 ms (gpt-oss-120b) to 33,763 ms (DeepSeek-V3.1). The accuracy figure was later
superseded by the channel scenarios and the two middleware fixes; **the latency
spread should not be read as a model property** — a later run of the same code put
DeepSeek at 2,172 ms (see Surprises: MARA serving variance).

What did hold across every run: bounded multi-hop traversal, cycle detection, and
grounded single-fact lookup succeed on all models — the capabilities a vector-RAG
baseline cannot reproduce.

**Track 7 reading:** the graph and ontology never changed while the LLM was swapped
repeatedly, and accuracy stayed within one scenario of each other. That supports
"the LLM is replaceable compute". Ranking the models by latency on this harness does
not.

## Surprises & Discoveries

> A complete record of every wrong turn — measurements that were wrong and looked fine,
> fixes that made things worse, bugs I introduced, methodology errors, and environment
> traps — is in [finbench-failure-log.md](finbench-failure-log.md). The pattern across
> them: almost every mistake produced a plausible number or a green test, and none was
> caught by the metric it was expressed in.


- **Outcome-only scoring misattributes middleware bugs to models.** Of the four
  distinct failures observed on the channel scenarios, only one was the model's
  fault. `laundering_ring_channels` failed on *all three* models because the
  `list_all` template discarded correct slots (seocho-pl1) — the LLM had already
  produced `{relationship_type: USES_CHANNEL, target_label: Channel}` correctly.
  A second "failure" was a scoring artifact (a model answered with the channel's
  Korean label instead of its code). This is the strongest argument for the
  stage-wise instrumentation: slot-fill 100% with accuracy 0% means the middleware
  is broken, while slot-fill failure with accuracy 0% means the model is — and
  outcome metrics report both as "0%".
- **A model can name the right relationship and still reverse it.** DeepSeek-V3.1
  emitted `anchor_label=Channel` for `USES_CHANNEL` (declared `Account -> Channel`).
  The Cypher was valid, passed label validation, and matched zero rows — a silent
  wrong answer. The ontology already knew the direction, so the guardrail can repair
  it; `last_orientation_repair` makes the rescue countable rather than anecdotal.
- **Recall-only scoring rewards over-answering.** After the direction repair,
  DeepSeek returned all twelve channels: recall 1.0, precision 0.25. It "passed" a
  containment check without answering the question. `exact` now requires both.
- **Latency comparisons across a provider are unstable.** DeepSeek-V3.1's p50 moved
  33,763 ms → 2,172 ms between runs with no code change (MARA serving variance).
  Accuracy and slot-fill are comparable across models; latency is not, and claiming
  a latency ranking as a model property would be wrong. Cross-provider (MARA vs
  Moonshot/kimi) is worse still — different serving stacks.
- **A bulk import leaves a root-owned store.** `docker exec` runs as root, so the
  neo4j process (uid 7474) cannot read the imported database; it surfaces only as
  "Unable to start" with an `AccessDeniedException` in debug.log. The `chown` step
  is required, not hygiene.
- **Bad benchmarking methodology understated our own result.** Without warm-up and
  with single-shot timings, the tuned/naive latency gap at SF1000 read as 67x; with
  `apoc.warmup.run` and 10 repetitions it is **411x** (1.8 ms vs 814 ms). The
  cold/warm ratio explains it: tuned 32.8 -> 1.8 ms (18.6x) but naive 841.8 -> 814.3
  ms (1.0x). **Warm-up helps the good plan and does nothing for the bad one** — an
  index seek is I/O-bound until its pages are cached, a full scan does the same work
  either way. Skipping warm-up therefore systematically understates the advantage of
  the better plan.
- **Page cache was at the 512M default against a 2.45 GiB store** — two orders of
  magnitude short of Neo4j's "store + growth + 10%" guidance, which made every
  absolute latency taken before `docker-compose.finbench.yml` unreliable. dbHits was
  unaffected, which is the argument for reporting it.
- **DuckDB `CAST(x AS BIGINT)` rounds** (not truncates); id generation must use
  `floor()` or it emits out-of-range endpoint ids.
- **`DISTINCT` + window function is not deterministic** in row order, which changed
  the Parquet checksum between identical runs. `GROUP BY` + `ORDER BY` fixed it.
- **Relationship-aware aggregation is a middleware gap.** All three models fail
  `fan_in_smurfing` identically, answering `2039` (the total Account count). The
  generated Cypher was `MATCH (n:Account) ... RETURN count(n)` — the intent path
  mapped "how many accounts …" to a *count-nodes-of-label* template and dropped
  the `TRANSFER` relationship + hub filter. Because it's deterministic given the
  intent classification, the model is irrelevant. This is the sharpest finding:
  a concrete limitation to fix, isolated by holding the graph constant and varying
  only the model.
- **Bad benchmarking methodology understated our own result.** Without warm-up and
  with single-shot timings, the tuned/naive latency gap at SF1000 read as 67x; with
  `apoc.warmup.run` and 10 repetitions it is **411x** (1.8 ms vs 814 ms). The
  cold/warm ratio explains it: tuned 32.8 -> 1.8 ms (18.6x) but naive 841.8 -> 814.3
  ms (1.0x). **Warm-up helps the good plan and does nothing for the bad one** — an
  index seek is I/O-bound until its pages are cached, a full scan does the same work
  either way. Skipping warm-up therefore systematically understates the advantage of
  the better plan.
- **Page cache was at the 512M default against a 2.45 GiB store** — two orders of
  magnitude short of Neo4j's "store + growth + 10%" guidance, which made every
  absolute latency taken before `docker-compose.finbench.yml` unreliable. dbHits was
  unaffected, which is the argument for reporting it.
- **DuckDB `CAST(x AS BIGINT)` rounds** (not truncates); id generation must use
  `floor()` or it emits out-of-range endpoint ids.
- **`_superseded_by` supersession predicate is Kùzu-only-breaking** (seocho-dgf):
  on DozerDB it is a harmless "property does not exist" warning (schemaless);
  Kùzu rejects the undeclared property at bind time. The primary path is fine.

## Decision Log

- Direct typed load over PG→LPG projection for the slice: the relational→LPG
  `ProjectionRule` mapper (ADR-0154 §4) does not exist yet; direct `GraphStore.write`
  is correct for a scalability benchmark where the graph is already typed. The
  projection ADR is a follow-up and is deliberately left unnumbered until it is
  written: reserving an id in advance is what produced the three-way claim on
  id 0155 (see ADR-0156), and `scripts/ci/check_adr_index.py` now rejects a
  cited id that has no file.
- DozerDB (primary serving path), not embedded Kùzu/Ladybug: the experiment is
  "real graph DB ↔ agent," and this sidesteps seocho-dgf.
- `query_mode="graph_cot"`: the default semantic path uses an entity-lookup
  template (keyed on name/uri) that our AML analytics questions don't fit;
  graph_cot invokes bounded Text2Cypher which generates traversal/aggregation.

## Validation And Acceptance

```bash
# B1 generate SF1 (deterministic)
python scripts/finbench/gen_duckdb.py --sf 1 --out outputs/finbench

# B1+B2 unit tests
.venv/bin/python -m pytest tests/seocho/test_finbench_gen.py tests/seocho/test_finbench_load.py -q

# start DozerDB (the apoc-extended-init sidecar exits 23; --no-deps bypasses it)
docker compose up -d --no-deps neo4j

# bulk load — the only viable path above SF10 (SF1000 = 36 s end to end)
python scripts/finbench/gen_duckdb.py --sf 1000 --out outputs/finbench
python scripts/finbench/bulk_load.py --src outputs/finbench/sf1000 \
  --database finbenchsf1000 --password "$NEO4J_PASSWORD"

# transactional loader (kept for small slices / API parity; ~142 rel/s)
python scripts/finbench/load_to_graph.py --src outputs/finbench/sf1 --target dozerdb \
  --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" --database finbenchsf1

# B3 graph-level gold check (exit 0 == all scenarios pass)
python scripts/finbench/verify_scenarios.py --src outputs/finbench/sf1 \
  --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" --database finbenchsf1

# B6 scale curve — tuned vs naive plan shape across scale factors
python scripts/finbench/scale_curve.py --scales 1,10,100,1000 \
  --src-root outputs/finbench --db-prefix finbenchsf \
  --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" \
  --out outputs/finbench/scale_curve_graph.json

# ontology ablation — what the middleware buys (labels-only vs full schema)
python scripts/finbench/ablation_ontology.py \
  --ontology examples/finbench/finbench.ontology.yaml --cases examples/finbench/cases.json \
  --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" \
  --database finbenchsf1 --model gpt-oss-120b

# B7 per-model breakdown, now stage-instrumented (needs MARA_API_KEY)
python scripts/finbench/mara_breakdown.py \
  --ontology examples/finbench/finbench.ontology.yaml --cases examples/finbench/cases.json \
  --uri bolt://localhost:7687 --user neo4j --password "$NEO4J_PASSWORD" --database finbenchsf1 \
  --models DeepSeek-V3.1,MiniMax-M2.5,gpt-oss-120b --out outputs/finbench/sf1/mara_breakdown.json
```

Acceptance: unit tests green; `verify_scenarios.py` exits 0; the breakdown writes
`mara_breakdown.{json,md}` with a per-model accuracy/latency table and per-scenario
matrix.

## Idempotence And Recovery

- Generation is deterministic (seed 0.42): re-running yields byte-identical Parquet.
- Loading into a fresh DB: `DROP DATABASE finbenchsf1 IF EXISTS; CREATE DATABASE finbenchsf1`
  then reload. `GraphStore.write` merges by node id, so reloading over an existing DB
  updates properties rather than duplicating.
- The breakdown is read-only against the graph; safe to re-run.

## Revision Notes

- v1 (this doc): B1–B4, B7 complete on SF1. B6 scale curve and the aggregation
  fix are open. Tracked alongside the BMT adapter beads (seocho-7lg family);
  seocho-dgf tracks the Kùzu supersession regression.
