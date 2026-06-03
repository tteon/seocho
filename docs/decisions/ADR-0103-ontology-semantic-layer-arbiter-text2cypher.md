# ADR-0103: Ontology-as-Semantic-Layer + Arbiter-Routed Decompose-then-Compile text2cypher

Date: 2026-06-03
Status: Proposed

## Context

text2cypher is the team's chronic pain point: the structured Cypher lane
returns **zero records ~70% of the time** (REAL record-count answerability
~0.30, ADR-0099 correction / answerability diagnosis), and every answer-path
sophistication we shipped this session (AnswerShape, RouteProfile, F8
multi-plan, scored grounding ADR-0099/0100/0101) measured **null** because
there was no non-empty structured result to operate on.

Two new measurements made the cause undeniable:

- **Prior-resistant SEC benchmark** (ADR-0102; recent 10-K XBRL facts, FY2025
  post-cutoff). On CLEAN synthesized fact sentences, grounded scores **1.00**
  — but only via the keyword **chunk fallback** (`SEOCHO_CHUNK_FALLBACK`,
  `1e131ac`), NOT structured Cypher.
- **Real 10-K MD&A narrative** (sec_filing_run): grounded collapses to
  **0.00** (0/15); the exact figure+scale is present in MD&A prose only 0.20
  of the time (the rest lives in Item 8 financial-statement tables). The
  graph contributes **nothing** on real filings today, and closed-book LLM
  (0.53) beats it.

A four-person expert panel (Wall-Street finance-data PhD; 20-year ontologist;
semantic-layer/OBDA architect; LLM text-to-query researcher; systems
integration architect) converged independently on one diagnosis and one fix,
corroborated by two references the team supplied:

- **dbt "semantic layer vs text-to-SQL" (2026):** a governed semantic layer
  scores 98–100% vs 84–90% for raw text-to-SQL. The LLM's job is reduced to
  *mapping NL → (metric, dimensions, filters) over a CLOSED vocabulary*;
  deterministic compilation guarantees correctness. **"Structural determinism
  beats reasoning."** Failure becomes an explicit error, not silent wrong data.
- **Text2SQL-Flow (arXiv 2511.10192):** SQL-aware data augmentation +
  **masked-alignment** structure-aware few-shot retrieval.

**The semantic layer IS our ontology.** The defect is that today the ontology
is not used as one: metric identity is a free-text `name` the LLM regenerates
per chunk, value is a STRING, period is a fuzzy string matched by `CONTAINS`,
entity is a `CONTAINS` match — so independent chunk extractions never MERGE
onto the same node, and even a perfect Cypher matches nothing.

## Decision

Make the ontology a **semantic layer** and split text2cypher into **Arbiter →
Decompose → Compile**, all behind a single source of truth shared by the
extraction writer and the query reader so they cannot drift. Additive,
env-gated (`SEOCHO_SEMANTIC_LAYER`, `SEOCHO_ARBITER`), MARA-first / bge-only.

### 1. Semantic-layer contract (the ontology as governed vocabulary)

Authored once per ontology package (offline governance), loaded into
`Ontology`:

- **MetricConcept taxonomy** — a closed SKOS-style vocabulary
  (`Revenue`, `NetIncome`, …) with `altLabels`; the LLM **selects**, never
  invents. Promotes the hardcoded `_FINANCE_METRIC_TERMS` (cypher_builder.py)
  into ontology data.
- **Entity key = CIK** (canonical); name/ticker are resolution inputs only,
  never identity.
- **Period model** — typed `(basis fiscal|calendar, fiscal_year, fiscal_period
  FY|Q, period_end DATE)` with a canonical key (`fiscal:2024:FY`); replaces
  `CONTAINS year`.
- **Reified Observation** — one node per `(entity, concept, period, unit,
  basis)` with a **deterministic key** `observation_key(...)` and a **typed
  numeric** `value_num`. This is the single node the metric pattern queries.

```
(:Company {cik})-[:HAS_OBSERVATION]->
(:Observation { obs_id,        // deterministic UNIQUE key — MERGE target
                concept_id, entity_cik, period_key, period_end DATE,
                value_num FLOAT, unit, basis, source_ref, _workspace_id })
(:Observation)-[:OF_CONCEPT]->(:MetricConcept)
(:Observation)-[:EVIDENCED_BY]->(:Chunk)        // provenance for narrative fallback
```

`observation_key(entity_cik, concept_id, period_key, unit, basis, workspace_id)`
is a pure SHA1 of canonical components. With a UNIQUE constraint, `MERGE
(o:Observation {obs_id})` is idempotent across chunks/documents/re-ingestion —
the fragmentation defect (each chunk minting a disjoint `name`-keyed node)
becomes structurally impossible.

### 2. Four coherence contracts (single source of truth)

A new data-plane package `src/seocho/semantic_layer/` holds the functions that BOTH
the writer (`index/observation_writer.py`, new) and the reader
(`query/…`) import — drift becomes impossible, then is locked by a contract
test:

- `concepts.py` — `ConceptRegistry.resolve(surface) -> concept_id` (closed).
- `keys.py` — `observation_key(...)` (deterministic identity).
- `periods.py` — `normalize_period(raw) -> period_key`.
- `identity.py` — `resolve_cik(name|ticker) -> CIK`.

`tests/test_semantic_contract.py` asserts writer-side and reader-side produce
**byte-identical** keys for the same input, that both import the key fn from
the same module path (no shadow copy), and that any concept the reader can
emit is a registry member. This test failing = extraction and query drifted.

### 3. The Arbiter (neutral measure → hint; does NOT decide)

`src/seocho/query/arbiter.py` (read-only, no synthesis LLM, no reasoning —
honors the "reasoning off the hot path" guardrail). Modeled on a scheduling
arbiter: it is the only component that sees BOTH grounding confidence (which
the compiler can't) AND graph contents (which the decomposer can't), so it is
the neutral vantage that routes — exactly as a memory-bus arbiter sees both
the latency-sensitive and throughput traffic that neither scheduler alone can.

**Measures** (cheap, bounded):
1. slot-resolvability via bge grounding (`ontology_grounding` +
   `make_fastembed_scorer`) — top score + threshold per slot, no LLM;
2. graph-content probe — ONE bounded read: does `(cik)` exist, does
   `(cik, concept)` have an Observation, `DISTINCT period_key`? (LIMIT, read-safe);
3. (v2) manifest-match — which registered semantic manifest best fits → `ontology_id`.

**Emits** a typed, read-only `ArbiterHint`:
```
ArbiterHint { route: STRUCTURED|NARRATIVE|HYBRID|CLARIFY|FAIL,
              ontology_id, concept_id?, entity_key?, period_keys?,
              missing_slots[], graph_has_data, available_periods[],
              confidence{per-signal}, rationale }
```
via a small **deterministic decision table** (auditable, hot-path-cheap) — the
single neutral home for the panel's "failure ladder":
- all slots resolve + `graph_has_data` → `STRUCTURED`
- slots resolve but that `(entity,concept,period)` is empty & narrative exists → `NARRATIVE`/`HYBRID`
- concept resolves, period missing → `CLARIFY` (+`available_periods`)
- entity/concept out-of-vocab → `NARRATIVE` if a corpus exists else `FAIL_EXPLICIT`

**Consumers decide, not the arbiter:** `planner.py` consults the hint just
before choosing a path; the decomposer/compiler/answering remain the deciders.
Because the arbiter shares the §2 contracts, a `STRUCTURED` hint guarantees the
compiler's exact-key Cypher will hit by construction. The arbiter's probe
result doubles as the repair hint (no second probe). The hint
(route + rationale + confidences) is a tracing span attribute → observability
of *why* each question routed where.

**This is what would have prevented the 0.00 MD&A disaster:** the arbiter
measures "concept resolves but no Observation exists for this
`(cik,concept,period)`" and routes `NARRATIVE`/`FAIL` instead of silently
returning an empty structured result dressed as an answer. dbt's "failure
looks like an error, not wrong data," implemented.

**MEASURED (S5, live decompose→arbitrate against the seeded DozerDB):**
"Apple revenue FY2024" (present) → `STRUCTURED`; "Apple revenue FY2099"
(absent) → `CLARIFY` offering available periods FY2023/24/25 (not a silent
empty); "why did gross margin expand" (OOV concept + qualitative) → `NARRATIVE`.
The neutral measure→hint routing works end-to-end; the planner remains the
decider.

**MEASURED (S4, full lane live: decompose→arbitrate→compile→execute→format
against seeded DozerDB, MARA + bge):** "Apple revenue FY2024" → STRUCTURED →
"$391,035 million (fiscal:2024:FY)"; "Apple net income **FY2025**" → STRUCTURED
→ "$112,010 million" (the prior-stale, post-cutoff fact the model could not
know — now answered from graph STRUCTURE, not chunk text or priors); "revenue
FY2099" → CLARIFY (no answer); "why did margin expand" → NARRATIVE (no answer).
The closed loop confirms the session's goal: the graph contributes via
deterministic structured retrieval. `local_engine.ask` consults this lane under
`SEOCHO_SEMANTIC_LAYER` and only short-circuits on a STRUCTURED answer;
everything else falls through to the existing lane (behavior preserved).

**MEASURED (S12, PROFILE profiler, `profile_probe.py`):** with the
UNIQUE(obs_id) constraint + Company.cik index, the compiled exact-key Cypher
plans as a **NodeIndexSeek** (seek_rate 1.0, scan_count 0, max db_hits 23) — the
structured path is not only correct (DCC=1.0) but index-backed and O(1), not a
label scan. GOPTS Layer-1/2 confirmed for the observation_lookup pattern.

**Staged roadmap (resolves the multi-ontology fork):**
- **v1 (smallest slice):** single finance manifest; `route ∈ {STRUCTURED,
  NARRATIVE, CLARIFY, FAIL}`; `ontology_id` field present but constant
  `"finance"`. Gate `SEOCHO_ARBITER`.
- **v2:** register N manifests; arbiter selects `ontology_id` by max
  manifest-match — same interface, non-breaking.
- **v3:** tune decision-table thresholds from ablation data.

### 4. Decompose → Compile (the two phases the arbiter routes into)

- **Phase 1 — DECOMPOSE (MARA, guided):** NL → a validated `QuerySlots`
  object `{intent, metric_surface, entity_surface, period(structured),
  dimensions, filters, aggregation}`. vLLM path uses `guided_json`; **MARA
  path (default) is validate-then-repair** against a Pydantic schema (one
  re-ask on `ValidationError`). The LLM emits surface forms + structural
  intent only — never canonical IDs, never Cypher. Surface→canonical is
  deterministic bge grounding (`ground_metric_concept`, entity→CIK,
  `normalize_period`); a slot that doesn't clear threshold is marked
  `unresolved` (→ arbiter `CLARIFY`/`FAIL`).
- **Phase 2 — COMPILE (deterministic):** resolved slots →
  `pattern_catalog.match(intent)` → `cost_model` rank →
  `PatternSpec.template_factory` emits exact-key Cypher (`=`/`IN` on indexed
  `obs_id`/`concept_id`/`period_key`, params bound, no `CONTAINS`, no free
  generation). `value_num` FLOAT makes deltas/ranges/aggregates real.

- **Few-shot (Text2SQL-Flow masked alignment):** `NLCypherExampleStore`
  records gain a masked skeleton (`[LABEL]/[REL]/[PROP]/[VALUE]`) + a bge
  embedding; retrieval is structure-aware k-NN; examples are injected as
  `masked_question → slots` pairs (few-shot the decomposition, not raw Cypher).
- **Augmentation (MARA, no OpenAI):** seed PatternSpec templates → 6 structural
  transforms → MARA generates paired NL in multiple styles → filter by
  executability (DozerDB) + NL↔Cypher correspondence (**MARA judge**) → mask →
  bge embed → store. Bootstraps the few-shot corpus cheaply.

### 5. MARA-first / bge-only

All LLM calls (decompose, augmentation, **judge**) use `provider="mara"`
(MiniMax-M2.5). All embeddings use fastembed `BAAI/bge-small-en-v1.5`
(`make_fastembed_scorer`; lexical fallback when absent). The one OpenAI risk is
`store/vector.py` defaulting `embedding_provider="openai"` — a small fastembed
adapter removes OpenAI from the path entirely. (See
[[feedback_mara_first_minimize_openai]].)

## Validation protocol

**Primary metric — Structured-Retrieval Hit-Rate (SRHR), fallback-OFF:** the
fraction of questions where the deterministically-compiled Cypher returned the
correct typed Observation row (numeric `value_matches` on the returned row +
`period`/`concept` match), measured with `SEOCHO_CHUNK_FALLBACK=0` and no
vector store. Scored on the **rows**, not the answer text, so the LLM cannot
lexically game it (the AnswerShape trap). Requires instrumenting the executor
to surface raw result rows.

**Sub-metrics (attribute where SRHR fails):** Slot-Resolution Accuracy (SRA,
deterministic label match — no judge), Deterministic-Compile Correctness (DCC,
oracle slots → templates), Repair-Recovery Rate; retained Prior-Staleness Delta
(structured, on FY2025 post-cutoff rows) and Temporal-Resolution Rate
(`wrong_year` is the canonical-period signal). **MARA judge is a guard only**
(must move WITH SRHR on stale rows; token-F1/exact demoted to diagnostics).

**Ablation (each adds one component, fallback-OFF):** A0 free-form baseline
(~0.30) → A1 closed-vocab concept → **A2 reified Observation key (expected
largest jump)** → A3 CIK entity resolution → A4 canonical period (`wrong_year`↓)
→ A5 few-shot decompose → A6 repair. If the big jump is at A5 not A2, the gain
is prompt-driven not structural — the matrix makes misattribution visible.

**Datasets:** Tier 1 prior-resistant CLEAN (have it, regression anchor); Tier 2
REAL 10-K MD&A **+ Item 8 tables** (the 0.00 floor — tables MUST be ingested or
the answer isn't in the corpus); Tier 3 held-out disjoint companies (run once,
anti-overfit).

**Targets (fallback-OFF, Tier 3):** SRHR 0.30 → ≥0.75 (kill <0.50);
MD&A+Item8 grounded 0.00 → ≥0.50 table-backed; Prior-Staleness Delta ≥+0.40;
Temporal-Resolution ≥0.80 / `wrong_year` ≤0.10; DCC ≥0.95.

**Run order:** (1) DCC on Tier 1 with oracle slots — no LLM/judge, confirms the
schema+templates can return the row at all (if <0.9, stop and fix templates).
**MEASURED (S2, `dcc_probe.py`): DCC = 1.00 (102/102, 20 companies, no LLM)** —
reified Observation + exact-key compiled Cypher returns the correct typed
`value_num` for every case, vs the legacy free-text-keyed Cypher's ~0.30. The
core thesis (the bottleneck was identity/structure, not Cypher) is confirmed;
the compile target is correct, so the LLM layers (arbiter, decompose) can
proceed. **MEASURED (S6, `sra_probe.py`): joint SRA = 1.00 (102/102, MARA
MiniMax-M2.5 decompose + bge resolution, no graph writes)** — concept, entity
(→CIK), and period all resolve correctly for every question. Composing the two
separately-measured stages, **SRHR ≈ SRA × DCC ≈ 1.00** on the prior-resistant
SEC set, vs the legacy free-text lane's ~0.30. HONEST CAVEAT: this set uses a
clean, well-formed question template ("What was {Company}'s {metric} for fiscal
year {Y}?") whose metric/entity/period are in the closed vocab and explicitly
stated, so SRA is an upper bound; varied phrasing, out-of-vocab metrics, and
ambiguous entities will be lower and are exactly what the arbiter (S5) routes to
CLARIFY/FAIL and what Tier 3 / real-question runs will measure. A closed-loop
e2e SRHR (decompose→resolve→compile→execute→score on rows) is the natural
confirmation. Then:
(2) ablation A0→A6 on Tier 1; (3) prior-staleness/temporal on stale rows; (4)
build Item 8 table ingestion, run on real noise; (5) freeze, run Tier 3 once;
(6) fallback-ON only to quantify the residual structure-vs-chunk gap.

## Consequences

Positive:
- Restores deterministic structured retrieval (the existing careful
  `cypher_builder`/`pattern_catalog`/`cost_model`/`ontology_grounding`
  machinery mostly "just works" once the data model is addressable).
- The arbiter turns silent empty results into explicit, observable routing
  (STRUCTURED/NARRATIVE/CLARIFY/FAIL) — and generalizes to multi-ontology
  selection without an interface change.
- Honest graph-contribution measurement becomes possible (SRHR fallback-OFF),
  closing the gap that made every prior leg measure null.

Negative / honest:
- This is the largest change of the session — additive and env-gated, but it
  touches extraction write, ontology, query, and store. Sequenced as the
  smallest-coherent-slice (one concept, Revenue) end-to-end first.
- It does not, by itself, get facts out of Item 8 tables — table ingestion is a
  required, separate piece (Tier 2). Without it the 0.00 floor is unliftable.
- Chunk fallback is re-scoped from answer-engine to gated narrative/provenance;
  the "1.00 on synthetic" was never structured contribution.

## Migration (additive, env-gated)

`SEOCHO_SEMANTIC_LAYER` (master) + `SEOCHO_ARBITER`; default OFF → today's path
byte-identical. Smallest first slice (one PR, Revenue only, end-to-end):
`semantic/` package (concepts/keys/periods/identity/slots) → `observation_key`
+ unit tests → `observation_writer` dual-writes Observations →
`ensure_constraints` UNIQUE `(obs_id,_workspace_id)` → `arbiter.py` v1 +
`decompose.py` + `pattern:observation_lookup` → `tests/test_semantic_contract.py`
→ tracing spans. Validate "What was {Company}'s revenue in FY2023?" returns a
typed `value_num` via exact-key Cypher — the first non-zero **structured** graph
contribution on real filings. Per [[feedback_use_beads_when_developing]], one
`bd` ticket per slice item.

CI: `run_basic_ci.sh`, `check-module-ownership-contract.sh` (new package),
`check-doc-contracts.sh`.

## Implementation Notes

- new (data-plane): `src/seocho/semantic_layer/` (concepts, keys, periods, identity,
  slots), `src/seocho/index/observation_writer.py`,
  `src/seocho/query/arbiter.py`, `src/seocho/query/semantic/decompose.py`.
- touched: `query/pattern_catalog.py` (`pattern:observation_lookup`),
  `query/cypher_builder.py` (`_observation_lookup`), `query/contracts.py`
  (`QuerySlots`, `ArbiterHint`), `query/ontology_grounding.py`
  (`ground_metric_concept`/`ground_dimension_value`), `store/graph.py`
  (`ensure_constraints`), `store/vector.py` (fastembed adapter), `ontology.py`
  (`.semantic` accessor), `tracing.py` (arbiter/decompose/compile spans).
- benchmark/eval: extend `eval/benchmark.py::run_query` to surface raw rows
  (for SRHR); reuse `scripts/benchmarks/sec_temporal_*` + Item 8 table ingestion.
- relates to: ADR-0102 (the prior-resistant benchmark that exposed the 0.00
  floor), ADR-0099/0100/0101 (the null answer-path legs this explains and
  subsumes for the financial path), ADR-0097 (cost model — ranks the compiled
  patterns), ADR-0090 (tiered nl2cypher — superseded for the financial path by
  decompose-then-compile).
