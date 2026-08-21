# Experiment manual — what runs, what it proves, and what is still assumed

Companion to `finbench-hypotheses.md`, written to be executable rather than argued. Every
hypothesis below states the code that tests it, the exact input and output, and what result
would refute it. Sections 6-9 document the machinery each one depends on: the models, the
prompts, the agent↔Neo4j path, and how plans are captured.

Status legend: **supported** · **refuted** · **partial** · **untested** (apparatus exists,
measurement not run) · **not testable** (apparatus missing).

---

## 1. H1 — Distribution dominates volume

| | |
|---|---|
| **Claim** | Growing the graph 1000x at fixed shape changes little; changing the degree tail at fixed size changes everything. |
| **Status** | **supported**, with one qualification below. |
| **Code** | `scripts/finbench/gen_duckdb.py` (`--sf`, `--hub-skew`), `scripts/finbench/scale_curve.py` |
| **Input** | `--sf 1,10,100,1000` at `--hub-skew 1`; then `--sf 1000 --hub-skew 3` |
| **Output** | `outputs/finbench/sf{N}/manifest.json` (`structural_profile`), `outputs/finbench/scale_curve.{json,md}` |
| **Measured** | Volume 1000x at fixed shape: db-hit profile unchanged. Degree max 31 → 158,315 at fixed volume: aggregate **45 ms → timeout**. |
| **Refuted by** | A volume increase at fixed distribution that degrades cost or accuracy. |
| **Qualification** | Accuracy *did* fall 100% → 67% from SF1 → SF1000 with code frozen, so volume is not inert. The defensible claim is "distribution dominates", not "volume is irrelevant". That 67% may have been the direction bug (§5) interacting with scale; **re-run needed**. |

```bash
python scripts/finbench/gen_duckdb.py --sf 1000 --hub-skew 3.0 --tag hub
python scripts/finbench/graph_properties.py --src outputs/finbench/sf1000-hub --update-manifest
```

---

## 2. H2 — The operation class decides, not the data

| | |
|---|---|
| **Claim** | On the same node, whether the answer can stop early decides whether the query returns at all. |
| **Status** | **supported — strongest result in the experiment.** |
| **Code** | `scripts/finbench/hub_degree_probe.py` |
| **Input** | `--database finbenchsf1000hub --manifest outputs/finbench/sf1000-hub/manifest.json` |
| **Output** | `outputs/finbench/hub_degree.{json,md}` |

Measured on one anchor, degree 158,315:

| shape | db hits | p50 | index seek |
|---|---|---|---|
| `DISTINCT … LIMIT 50` | 163 | 2.8 ms | yes |
| `ORDER BY ts DESC LIMIT 5000` per hop (FinBench truncation) | 11,502,593 | 2,723 ms | yes |
| `count(DISTINCT …)` | — | **timeout** | — |

**Expected benefit:** the routing signal an agent needs is terminability, which is free to
compute from the question. **Refuted by:** a terminable question that degrades with degree.

**Note the middle row.** FinBench's own mitigation, written as user-level Cypher, cost
**70,000x more than no mitigation**, because `ORDER BY` is a pipeline breaker that destroys
the laziness doing the protecting. This is why the gate (§4) emits *decisions*, never
rewritten queries.

---

## 3. H3 — Cost is predictable offline, before execution

| | |
|---|---|
| **Claim** | `L2` (sum of out-degrees over the anchor's out-edges), computed from Parquet, predicts engine db hits. |
| **Status** | **supported inside a stated range.** |
| **Code** | `scripts/finbench/curate_parameters.py` |
| **Input** | `--src outputs/finbench/sf1000-real --per-band 3 --validate --database finbenchsf1000real` |
| **Output** | `outputs/finbench/sf1000-real/curated_parameters.{json,md}` |

Per-anchor `measured_db_hits / L2`:

| L2 | ratio |
|---|---|
| 5 | 7.0 – 14.6 |
| 72 | 4.4 – 5.5 |
| 3,773 | 2.07 |
| 79,794 | 2.00 |
| 192,942 | 2.01 |
| 10,238,484 | 2.016 |
| 15,973,145 | 2.015 |
| 51,447,907 | **timeout** |

**The constant is ≈2.0 for L2 ≳ 3,800 and breaks below it**, where fixed per-query
overhead dominates. The break is in the harmless direction: it *under*-predicts cheap
queries. `DB_HITS_PER_L2 = 2.0` in `workload_gate.py` is calibrated for the range that
matters.

**Correction worth recording:** I first read the band-*mean* ratio at the `max` band as 1.02
and reported that the constant breaks at the extreme. It does not — the mean was skewed
because a timed-out anchor's L2 counted toward the estimate while its (absent) measurement
did not. Per-anchor ratios are the truth; band means are not.

**Expected benefit:** a gate can price a query with no round trip and no dependence on the
engine it is deciding about. **Refuted by:** a query family where L2 and db hits decouple —
untested candidates are edge predicates and hop counts above two, where the estimator
extrapolates from the observed branching factor and says so.

---

## 4. H4 — Anchor-local properties suffice to spot supernode risk

| | |
|---|---|
| **Claim** | Degree (or another node-local property) identifies the expensive anchors. |
| **Status** | **refuted.** |
| **Code** | `scripts/finbench/curate_parameters.py --baseline-degree` |
| **Output** | `curated_parameters.md`, "Baseline" section |

| anchor degree | measured db hits |
|---|---|
| 6 | 158,487 |
| 73 | 3,876 |
| 336 | 429,042 |
| 158,315 | timeout |

Holding degree *exactly* constant (cv **0.0**) left measured cost varying with cv **1.91** —
a standard deviation nearly twice the mean. Curating on L2 instead was **197x tighter** at
the medium band (cv 0.0097 vs 1.9068).

**Consequence:** "detect the supernode" is the wrong framing; cost follows the
neighbourhood. `workload_gate.py` therefore takes L2 and deliberately does **not** take
degree.

---

## 5. H5 — An ontology's vocabulary bounds what any prompt can convey

| | |
|---|---|
| **Claim** | Retrieval quality is limited by what the schema can express, not by prompt wording. |
| **Status** | **supported.** |
| **Code** | `src/seocho/ontology.py` (`RelDef.source_role/target_role`), `src/seocho/query/cypher_builder.py` (`_anchor_role`, `intent_extraction_prompt`), `src/seocho/query/hybrid_planner.py` (`schema_for_prompt`), `scripts/finbench/ablation_ontology.py` |
| **Input** | `--ontology examples/finbench/finbench.ontology.yaml --cases examples/finbench/cases_hub.json --database finbenchsf1000hub --model gpt-oss-120b`, `SEOCHO_QUERY_PRECEDENCE=by_route` |
| **Output** | `outputs/finbench/sf1000-hub/ablation_hub_final.{json,md}` |

| | full ontology | labels only |
|---|---|---|
| accuracy | 1/12 → **12/12** | **0/12** |
| held-out paraphrases | **3/3** | 0/3 |
| sargable | 100% | 0% |
| db hits | 1,102,545 → 537,477 | 64,001,516 (**119x**) |

Root cause was two conditions holding at once: `_count` hardcoded the anchor as the arrow's
*target* (right for `(Company)-[:HAS_METRIC]->(Metric)`, wrong for every outgoing question),
and `_orient_relationship` returns early when `source == target`, which `TRANSFER: Account →
Account` triggers — so the guardrail reported nothing to repair while emitting the reverse
of what was asked.

**The sub-result that matters most:** the first fix resolved direction by substring-matching
a hand-authored phrase list. It scored 12/12 on questions whose phrasings its author had
written into that list, and **0 of 6** on paraphrases; a miss returns `""` and falls back to
the old assumption, silently reinstating the bug. Overfitting a mechanism to its own test set
looks exactly like success. Moving the general case to the model — with the ontology
supplying the role *names* — is what made it generalise.

**Refuted by:** an ontology addition that improves accuracy without changing what the prompt
conveys.

---

## 6. H6 — Cardinality in the ontology changes agent behaviour

| | |
|---|---|
| **Claim** | Telling the model the degree distribution makes it prefer bounded shapes. |
| **Status** | **untested.** Apparatus built, measurement not run. |
| **Code** | `scripts/finbench/annotate_ontology_degrees.py`, `RelDef.degree_hint` |
| **Input** | `--src outputs/finbench/sf1000-real --ontology examples/finbench/finbench.ontology.yaml` |
| **Output** | in-place `degreeHint` block in the ontology (comments preserved, re-parse verified) |

```yaml
degreeHint:
  median_out: 3
  p99_out: 41
  max_out: 77036
  max_over_median_out: 25678.7
  heavy_tailed: true
  measured_from: outputs/finbench/sf1000-real
```

Derived, not asserted — `measured_from` records which snapshot, and relationships whose
degree carries no information (one `OWN` per account) get nothing. The `heavy_tailed`
threshold of 50x comes from the measured contrast: the uniform graph sits at max/median ~3
with no hub effect, the power-law graph at ~25,700 and times out.

**Expected benefit — deliberately modest.** The model cannot make `count(DISTINCT)` cheap on
a hub. The plausible effect is on *choice*: preferring a terminable shape where one exists.

**How to test it:** ablate the hint alone with roles held constant, and score **the share of
questions answered with a terminable shape plus db hits per answer** — not accuracy.
Accuracy is the wrong metric here because both arms can be correct at different costs.
**Refuted by:** identical shapes and identical db hits with and without the hint.

---

## 7. H7 — Route on engine capability, not operation name

| | |
|---|---|
| **Claim** | "Traversal ⇒ graph engine" is the wrong rule; what matters is which engine serves the operation cheaply. |
| **Status** | **partial** — the naive rule is refuted; the capability rule is supported from one side. |
| **Code** | `scripts/finbench/arm_sql_vs_cypher.py`, `ENGINE_CAPABILITIES` in `workload_gate.py` |
| **Output** | `outputs/finbench/arm_sql_vs_cypher.{json,md}` |

text2SQL **88%** against text2Cypher **25%** on identical questions and data, and DuckDB
answered traversal questions with `WITH RECURSIVE`, including a monotonic-time condition the
Cypher arm never produced. I predicted the opposite and retracted it.

The capability side is supported by the 70,000x truncation result (§2): a bound the engine
cannot serve natively is an amplifier.

**Still needed:** a capability matrix *measured* rather than declared — per engine, does it
serve ordered top-K expansion, lazy limit, set-oriented join, recursive traversal, and at
what cost. `ENGINE_CAPABILITIES` currently encodes two engines from this experiment's
measurements and is explicitly a stub.

---

## 8. H8 — A pre-flight gate prevents the baseline bottleneck

| | |
|---|---|
| **Claim** | Pricing a question before execution prevents the runaway, without refusing cheap work. |
| **Status** | **supported on first validation** (see §8.2 for the caveats). |
| **Code** | `scripts/finbench/workload_gate.py` |
| **Input** | `--src outputs/finbench/sf1000-real --cases examples/finbench/cases_hub_real.json --budget-rows 200000 --validate --database finbenchsf1000real` |
| **Output** | `outputs/finbench/sf1000-real/workload_gate.{json,md}` |

### 8.1 What it computes

Five inputs → one of four actions. **Never a rewritten query** — §2's 70,000x result makes
"emit a cleverer bound" the failure mode this exists to avoid.

| input | source | why |
|---|---|---|
| operation class | question text (`classify`) | terminability is a property of the question |
| predicted db hits | `L2 x 2.0` from the snapshot | §3 |
| terminable | operation class | §2 |
| bound safe | operation class | `LIMIT 50` on "how many counterparties" is a wrong answer, not an approximation |
| engine capability | `ENGINE_CAPABILITIES` | §7 |

Actions: `execute` · `execute_bounded` · `approximate` · `decline`.

### 8.2 Two invalidations found by validating rather than trusting

**The first run was meaningless.** Cases curated on `sf1000-hub` were priced against
`sf1000-real`'s cost model. Anchor ids do not carry across snapshots, so every anchor looked
cheap and every verdict was `execute`. Cases now record `curated_from` and the gate
**refuses on mismatch** instead of producing confident numbers about nothing.

**The decisive anchor was missing.** Bands stopped at the 99.9th percentile (L2 192,942)
while the actual maximum was **51,447,907** — 266x higher. A `max` band now exists, and that
anchor is the only one in the set that times out.

**And a bug the matrix exposed.** Anchor extraction required 4+ digits, so `account number 0`
— the single most expensive anchor in the dataset — parsed as *unanchored*. It failed safe
but for the wrong reason, and would misclassify every low-numbered account. Named-form
extraction (`account number (\d+)`) now runs first.

### 8.3 Verdict against outcome

Errors are asymmetric and the matrix is read accordingly: a **false clear** (cleared, then
did not return) is the failure the gate exists to prevent; a **false flag** trades answer
quality for safety and is a tuning question.

```bash
python scripts/finbench/workload_gate.py \
  --src outputs/finbench/sf1000-real \
  --cases examples/finbench/cases_hub_real.json \
  --budget-rows 200000 --validate \
  --database finbenchsf1000real --password "$NEO4J_PASSWORD"
```

Validation executes **the shape the gate reasoned about** (`SHAPES` in the module), not
whatever an LLM happened to emit — scoring against generated Cypher would measure the model
rather than the gate.

Result over 13 cases on `sf1000-real`, budget 200,000 predicted db hits:

| | actually within budget | actually over / timeout |
|---|---|---|
| **gate cleared** | **11** ✓ | **0 false clear** |
| **gate flagged** | **0 false flag** | **2** ✓ |

100% agreement. The decisive pair is one anchor — account 0, L2 51,447,907 — split by hop
count alone:

| case | verdict | actual | db hits |
|---|---|---|---|
| `max_fan_out_count` (1 hop) | execute | returned | 154,077 |
| `max_twohop_count` (2 hops) | approximate | **timeout** | — |

Same node, same data, same code; the gate separates them before either runs. That is H2
reproduced one layer up, which is the point of the gate existing.

Prediction accuracy, as actual / predicted: **1.00 – 1.10 for L2 ≳ 3,800** (medium through
max) and **2.2 – 2.3 below it**, matching §3's stated range — the estimator under-predicts
cheap queries and is accurate where the decision matters.

**Refuted by:** any false clear. **Still needed:** the unanchored ring question, the first
case where the correct output is not a query at all — 471,151 candidate rings, no anchor, no
early exit.

---

## 9. H9 — Driver-level enforcement adds what query-level cannot

| | |
|---|---|
| **Claim** | A bolt/Rust layer is worth building. |
| **Status** | **untested, and the usual justification is wrong.** |

The graph engine is **0.3–0.9%** of end-to-end agent latency (122 ms against 37,083 ms of
LLM time). **Latency does not justify this work.** What might:

- **Early-abort streaming.** Lazy `DISTINCT … LIMIT` is what saved the hub case (163 db hits
  at degree 158,315). At the driver level that becomes explicit and controllable, including
  for queries the model left unbounded.
- **Transaction bounds.** `session.run(q, timeout=N)` silently becomes a query *parameter* in
  the Python driver; the bound belongs on the transaction. I made this mistake **twice**, the
  second time after documenting it — an argument for an API where it is hard to get wrong.
- **Per-query row and memory accounting**, with a kill at a predicted budget.

**What it cannot fix:** aggregates. `count(DISTINCT)` on a hub must walk the neighbourhood.

**First measurement to take:** driver overhead as a share of server time. If ~0, the latency
argument dies by data and the work is justified by enforcement or not at all.

---

## 10. H10 — Quadrant routing beats a single engine

**Status: not testable yet.** Wired today: DuckDB (columnar OLAP) and DozerDB (graph OLTP).
Graph-OLAP is absent; row-store OLTP is not represented. A quadrant cannot be claimed from
two points.

**Cheapest credible third arm:** precomputed summaries — FinBench's factor tables, which
`curate_parameters.py` and `graph_properties.py` already approximate — because that is what
the unanchored motif question needs and neither current engine can answer within budget.

---

## 11. Models and prompts

### 11.1 Models

| | |
|---|---|
| Provider | MARA, OpenAI-compatible, `https://api.cloud.mara.com/v1` |
| Auth | `MARA_API_KEY` in `.env` |
| Constructed by | `create_llm_backend(provider="mara", model=...)` — `src/seocho/store/llm.py` |
| Primary model | `gpt-oss-120b` |
| Comparison models | `DeepSeek-V3.1`, `MiniMax-M2.5` |
| Temperature | `0.0` for intent extraction, with `response_format={"type":"json_object"}` |

**Caveat carried from an earlier measurement:** ranking models by latency on this provider is
unsafe — DeepSeek-V3.1 measured p50 33.8 s and 2.2 s on identical code in different runs.
Accuracy comparisons are stable; latency comparisons are not.

Accuracy across models on the original nine cases (SF1, after middleware fixes):
DeepSeek-V3.1 89%, MiniMax-M2.5 89%, gpt-oss-120b 100%.

### 11.2 Prompt 1 — intent extraction (no Cypher generated)

`CypherBuilder.intent_extraction_prompt()`, `src/seocho/query/cypher_builder.py`. Assembled
from the ontology, so **its content is a function of the schema**, which is the mechanism
behind §5. Blocks, in order:

1. Task: extract intent and structured fields, **do not generate Cypher**.
2. Ontology profile: `package_id`, `version`, `graph_model`, supported deterministic intents.
3. Question-scoped schema hints (`derive_schema_hints`): candidate labels, relationships,
   properties for *this* question.
4. Node types with descriptions and property lists.
5. Relationship types as `(Source)→(Target) — description`. **Only these exist.**
6. **Directional roles** — emitted only for relationships that declare them:
   ```
   Directional roles (both endpoints may share a label, so the arrow — not the
   label — carries the direction):
     - TRANSFER: the tail of the arrow is the sender, the head is the beneficiary
   ```
7. **Degree distribution** — emitted only where `degreeHint.heavy_tailed`:
   ```
   Degree distribution (measured on the loaded data):
     - TRANSFER: median out-degree 3, p99 41, maximum 77,036 — a few nodes carry
       orders of magnitude more edges than the typical one
     Multi-hop expansion from a high-degree node is unbounded work. Prefer an intent
     whose answer can stop early …
   ```
8. Output JSON contract: `intent`, `anchor_entity`, `anchor_label`, `target_entity`,
   `target_label`, `relationship_type`, `metric_name`, `years`, and `anchor_role`
   (`source` | `target` | empty) when roles are declared.
9. Verification instructions and worked examples.

Both blocks 6 and 7 are **conditional on the ontology declaring the facts** — an ontology
that never needed the distinction is not handed a field it cannot answer.

### 11.3 Prompt 2 — validated Cypher generation

`schema_for_prompt(ontology, policy)`, `src/seocho/query/hybrid_planner.py`. A dict of schema
entries plus three synthetic keys:

- `__tenant_scope__` — states the tenant convention explicitly. Without it the model invented
  a `Workspace` node to satisfy the scope check and the guardrail correctly refused: an
  earlier arm scored **0%** for exactly this reason, measuring the prompt rather than the
  model.
- `__direction__(REL)` — the declared arrow direction and an instruction not to go
  undirected unless the question is symmetric. Added after 2-hop counts came back inflated
  **21x** on a small anchor from undirected traversal.
- `__cardinality__(REL)` — the heavy-tail facts and a preference for shapes that stop early.

### 11.4 Routing between the two

`SEOCHO_QUERY_PRECEDENCE` (`src/seocho/query/hybrid_planner.py`):

| value | behaviour | measured (SF1000, 9 cases) |
|---|---|---|
| `template_first` | pattern catalog only (historical default) | 89% |
| `by_route` | `RouteProfile` decides per question; only routes declaring `VALIDATED_GENERATION` use the LLM | **100%** |
| `generated_first` | generation for everything (measurement switch) | 89% |

---

## 12. Agent ↔ Neo4j interaction

### 12.1 The path

```
client.ask_response(question, database=…, reasoning_mode=…, repair_budget=1,
                    query_mode="graph_cot")            src/seocho/client.py
  → local_engine.ask()                                 src/seocho/local_engine.py
      → _build_planner(ontology, database=…)           precedence decides the planner
      → planner.plan(question)                         src/seocho/query/planner.py
          → LLM call 1: intent extraction (§11.2)      temperature 0, json_object
          → normalize_intent + derive_schema_hints     anchor_role resolved here
          → CypherBuilder.build(...)                   template, or
          → generate_validated_cypher(...)             LLM call 2 + EXPLAIN validation
      → GraphStore execute (bolt)                      one session per query
      → _should_repair(records, intent_data)?          repair_budget=1 → one retry
      → _annotate_plan_quality(span, …)                sampled PROFILE (§13)
      → synthesis LLM call
```

Two LLM calls minimum (intent, synthesis), three when generation is routed to, four with a
repair. **The graph round trips are the cheap part**: 122 ms of engine against 37,083 ms of
model time.

### 12.2 Guardrails in the path, and where each one binds

| guardrail | binds on | measured effect |
|---|---|---|
| allowed labels / relationship types | generated Cypher, pre-execution | rejects hallucinated schema |
| `max_graph_hops: 4` | pattern depth | bounds path templates |
| `max_result_rows: 50` | **the result**, not the traversal | protects only terminable questions — an aggregate walks the whole neighbourhood regardless |
| `EXPLAIN` pre-flight | generated Cypher | syntax + schema validity, **not cost** |
| direction orientation | template endpoints | returns early when `source == target` — the gap §5 closed |
| workspace scope | every matched node | inline `{_workspace_id: $workspace_id}` |

**The load-bearing gap:** nothing in this list prices the query. That is what §8 adds, and
why `EXPLAIN` is not sufficient — it validates, it does not estimate.

### 12.3 Design directions this measurement supports

- **Bound the traversal, not the result** — but only with an operation the engine serves
  natively (§2's 70,000x).
- **Price before planning.** The gate runs on the question and the snapshot, so it needs no
  round trip and can choose the engine before a query exists.
- **Index and label everything anchored.** Plan shape was **264,005x** db hits at SF1000 with
  *identical answers*, so this is the largest single lever measured.
- **Sizing input is the largest neighbourhood served, not the store size.** Page cache
  showed no effect on a degree-less graph and **2.3x** on large neighbourhoods; the number to
  size against is computed by `curate_parameters.py`.

### 12.4 Parallelism — what exists and what does not

Present: `asyncio.gather` for enrichment fan-out (`src/seocho/agent/enrichment_router.py`),
`ThreadPoolExecutor` in `src/seocho/evaluation.py`, FastAPI threadpool offload in
`src/seocho/http_runtime.py`.

**Not present: parallel or partitioned execution of a single graph query.** No `CALL {…} IN
TRANSACTIONS`, no query splitting across sessions, no concurrent sub-query fan-out inside one
question. Every measurement in this experiment is single-query, single-session.

That is an untested axis, and the honest expectation is mixed: parallelising a hub expansion
divides wall-clock but not total work, so it helps a latency budget and not a cost budget —
and the timeout cases fail on work, not on latency. Worth measuring, not worth assuming.

---

## 13. How query plans are captured and logged

### 13.1 In the serving path — sampled PROFILE

`_annotate_plan_quality`, `src/seocho/local_engine.py:~680`.

| | |
|---|---|
| Mode | `off` \| `slow` \| `all` |
| `slow` threshold | `SEOCHO_PROFILE_PLAN_THRESHOLD_MS`, default **250 ms** |
| Write safety | skipped when the query contains `create`/`merge`/`delete`/`set`/`remove` — re-running a write would double-apply |
| Mechanism | re-runs `PROFILE <cypher>` on a fresh session, **drains the result** so the profile is complete |
| Failure policy | wrapped in `except Exception: return` — diagnostics must never break a request |

The plan is re-run rather than captured inline, which costs a second execution. That is the
deliberate trade: `PROFILE` on every request would tax the fast path, and the shape of a slow
query is what needs explaining.

### 13.2 What is extracted

`summarize_profile`, `src/seocho/query/plan_quality.py` — flattens the PROFILE tree into
`operators`, summed `db_hits`, summed `rows`, and a **strict** `sargable` flag: *one* scan
anywhere in the plan marks the query non-sargable, because a single scan component grows with
the graph even when the rest is indexed.

### 13.3 Where it goes

- **Spans** — `span_attributes(summary)` onto the `rag.execute` span, with
  `db.plan.sampled=true`. Exported OTLP → Tempo.
- **Metrics** — `record_metrics(summary)` → Prometheus, so sargable rate and db hits per
  answer can be watched for drift as the graph grows. A span explains one request; the metric
  says whether the fleet is degrading.
- **Experiment harnesses** — `profile_cypher` in `scripts/finbench/instrumentation.py`
  captures the same signals per case into the S1–S5 stage fingerprint.

### 13.4 Why db hits and not latency

At SF1 the failing plan shape is 4.3 ms against 17 ms, and **unwarmed it reads as nothing**
(20 ms vs 19 ms) — while db hits already differ **269x**. Db hits is the leading indicator;
low-scale latency is not. The Grafana panel to watch is sargable rate and db hits per answer,
not p95.

### 13.5 Known gaps in the capture

- `EXPLAIN` is used for validation only; its **estimated rows are never read**, so the
  planner's own cardinality estimate is available and unused. Comparing it against L2 is an
  obvious cheap experiment and has not been run.
- Sampling is threshold-triggered, so a *cheap-but-non-sargable* query below 250 ms is never
  profiled — precisely the query that is fine now and ruinous at 10x scale.
- Nothing records the plan of a query that **timed out**, which is the case most worth
  explaining.
