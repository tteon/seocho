# ADR-0104: Graph-as-Context Serializer Confound + Fair-Budget Graph≈Vector Tie (GraphRAG-Bench)

Date: 2026-06-11
Status: Proposed

## Context

Every prior content-vs-context measurement in this program (FinDER, BC3, and the
gold-triple GraphRAG-Bench run) reported **graph ≤ vector** on answer quality, and
the gold-triple GraphRAG-Bench run specifically reported a three-way **TIE**
(Graphiti ≈ SEOCHO ≈ vector) because handing every system the *same* gold facts
removes retrieval representation as a variable (synthesis ceiling).

Two confounds made those graph-lane numbers un-interpretable as a statement about
graph retrieval:

1. **Serializer confound.** SEOCHO's `_graph_context` serializer DROPPED the raw
   `Chunk`/`Section` text the graph already stores (`_INFRA_LABELS`), and the
   Graphiti lane was run structure-only (`facts`+`entities`, no `episodes`). Both
   graph systems were answering from typed structure with their stored raw text
   withheld — not a fair comparison against vector, which serves raw chunks. The
   `keep_raw` flag (commit `e0d80a6`) re-adds that text.
2. **Budget confound.** The first `keep_raw=True` serialization emitted **all** raw
   passages (the whole 225K-char novel, ~483K-char context per question) — far more
   raw text than vector (top-4 chunks) or Graphiti (top-k episodes) get, so any
   "win" could be context quantity rather than retrieval quality.

This ADR records the result of removing both confounds on the **official
GraphRAG-Bench** harness (not our own pipeline), so the conclusion is attributable
to retrieval representation rather than to SEOCHO-specific plumbing.

### Method (single-factor, §20-fair)

- Corpus: GraphRAG-Bench `Novel-8559`, 25 Complex Reasoning + 25 Fact Retrieval.
- Extraction + synthesis: MARA `MiniMax-M2.5` for all lanes (held constant); judge:
  MARA `gpt-oss-120b` (judge ≠ synthesis). Two independent scorers cross-validate:
  a single-call `fast_judge` (0/0.5/1) and the official ragas `answer_correctness` +
  ROUGE-L.
- Six answer-lanes on ONE shared per-system build (the two raw lanes reuse the build,
  no re-ingest; the `_topk` lane is per-query):
  - `vector` — BGE top-4 raw chunks (content baseline, ~24K chars)
  - `seocho` — typed graph, `keep_raw=False` (structure only, ~31K)
  - `seocho_keepraw` — typed graph + **all** raw passages (~483K, whole novel)
  - `seocho_keepraw_topk` — typed graph + the **same top-4 raw chunks vector gets**
    (relevance-ranked, budget-bounded, ~46K) — the fair-budget lane
  - `graphiti` — `facts`+`entities`, no episodes (structure only)
  - `graphiti_episodes` — `facts`+`entities`+`episodes` (Graphiti's native raw, ~64K)

### Results (n=25/type; fast_judge | ragas answer_correctness)

Complex Reasoning:

| lane | ctx | fast | ragas a_corr |
|---|---|---|---|
| seocho (structure only) | 31K | 0.08 | 0.191 |
| graphiti (structure only) | small | 0.24 | 0.291 |
| vector (top-4 chunks) | 24K | 0.74 | 0.403 |
| **seocho_keepraw_topk** (struct + same 4 chunks) | 46K | 0.74 | 0.465 |
| graphiti_episodes (top-k raw) | 64K | 0.86 | 0.415 |
| seocho_keepraw (whole-novel dump) | 483K | 0.94 | 0.495 |

Fact Retrieval:

| lane | ctx | fast | ragas a_corr |
|---|---|---|---|
| graphiti (structure only) | small | 0.24 | 0.325 |
| seocho (structure only) | 31K | 0.32 | 0.406 |
| vector (top-4 chunks) | 24K | 0.66 | 0.421 |
| **seocho_keepraw_topk** | 46K | 0.72 | 0.425 |
| seocho_keepraw (dump) | 483K | 0.76 | 0.464 |
| graphiti_episodes | 64K | 0.76 | 0.479 |

## Decision

Two findings, reported separately to keep observation distinct from interpretation:

1. **The prior "graph < vector" was a SERIALIZATION confound, not extraction recall
   or ontology.** Adding each system's stored raw text lifts answer quality on BOTH
   judges and BOTH systems: `seocho` struct→keepraw CR fast 0.08→0.94 / ragas
   0.191→0.495; `graphiti` struct→episodes CR fast 0.24→0.86 / ragas 0.291→0.415.
   Structure-only lanes are the worst; raw-bearing lanes reach or exceed vector.
   This reproduces on the official GraphRAG-Bench harness, not just our pipeline.

2. **At a fair raw budget, graph-as-context TIES vector — the typed structure adds
   ≈0 over raw.** `seocho_keepraw_topk` (typed structure + the *same* top-4 chunks
   vector gets) vs `vector`: fast CR +0.00 / FR +0.06; ragas CR +0.062 / FR +0.004.
   The two judges disagree on which slice carries the small edge → within n=25 noise.
   The earlier `seocho_keepraw` "win" (fast CR 0.94) was **context quantity**: cutting
   483K→46K drops fast CR 0.94→0.74 and ragas 0.495→0.465.

Therefore: **graph-as-context retrieval is not a quality differentiator over vector
on narrative multi-hop QA.** Retrieval-representation benchmarks (GraphRAG-Bench,
HotpotQA-style multi-hop) will keep returning a tie once budgets are fair, because
a strong synthesizer over equivalent raw text hits the same ceiling regardless of
representation. SEOCHO's differentiator is **sound logical inference (owlready2 /
SHACL entailment) + governance / determinism / audit / refusal**, which these
benchmarks do not measure. Multi-hop retrieval ≠ logical entailment.

## Consequences

- **`keep_raw` is the correct default** for any graph-as-context serialization: never
  drop the raw text the graph already stores. Structure-only serialization is the
  failure mode that produced every historical "graph loses" result.
- **Budget-bounded, relevance-ranked serialization is the product direction**
  (backlog #3): the `seocho_keepraw_topk` shape (typed structure + top-k relevance-
  ranked raw passages, matched to a fixed budget) replaces the unordered whole-graph
  dump. Do not ship or cite the whole-novel dump as a retrieval result.
- **Do not claim graph-retrieval superiority on narrative QA.** Report the tie. Any
  graph "win" on a retrieval-quality bench must first be checked for the serializer
  and budget confounds documented here.
- **Where to actually measure the differentiator** (separate track, future ADR):
  inference-required answers (LUBM/UOBM, ORE), deductive reasoning isolated from
  retrieval (ProofWriter/RuleTaker/FOLIO), and governance/refusal (the Answerability
  Gate held-out set: refusal precision/recall + silent-wrong rate). These are
  designed so retrieval alone is insufficient by construction.
- **Scope/limits (binding, §20.5–20.6):** single corpus (Novel-8559), n=25/type
  (underpowered); MiniMax-M2.5 synthesis is NOT numerically cross-comparable to the
  old kimi gold-triple run (different model + protocol); ragas absolutes are
  compressed (partial credit) vs fast_judge's harsher 0/0.5/1, though rankings agree.
- The comparison harness (`graphrag_bench_real_extract.py`, adapters, judges) stays
  **local/untracked** per the established commit policy; this ADR and the per-lane
  score artifacts are the shipped record. Artifacts:
  `outputs/evaluation/graphrag_bench/gbench-keepraw/{<lane>.json, fast_correctness.json, official_scores_<lane>.json}`.

## Related

- ADR-0102 (prior-resistance benchmark — motivated isolating graph contribution),
  ADR-0103 (semantic-layer/arbiter — the inference/governance lane this ADR points to
  as the real differentiator), and the `keep_raw` fairness fix (`e0d80a6`).
