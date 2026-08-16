# ADR-0213: Graph agentic-RAG stage breakdown — where end-to-end quality is lost

- Status: experimental (downgraded from accepted 2026-08-17 after multi-agent
  review — the attribution below is not yet defensible; see "Correction" and
  "Review caveats")
- Date: 2026-08-17
- Tickets: seocho-5ny (OS ablation epic, sibling instrument)
- Related: ADR-0144 (rag.ask span tree), #589 (relationship-endpoint remap),
  #574 (text2cypher scope), #575 (plan quality)

## Context

The session goal was a **graph agentic-RAG breakdown**: attribute end-to-end RAG
quality to its stages — `indexing → ontology → retrieval/text2cypher →
generation` — rather than reporting a single pass/fail number. Run e2e on hosted
MARA MiniMax-M2.7 + live DozerDB 5.26.3, with observability (ADR-0144 `rag.ask`
span tree).

A breakdown is only honest if **stage 1 actually writes what later stages
consume**. It did not: domain relationships were being dropped non-
deterministically at index-write time ("N extracted, 0 persisted"). That was
diagnosed and fixed first (#589) — otherwise every downstream number would
misattribute a stage-1 loss to retrieval or generation.

## How the drop was pinned (prerequisite, #589)

A per-stage **relationship-survival census** was added to the index write path
(`_relcensus` / `_resolvable_rels`, emitted as
`seocho.index.relationship_survival.count`). One live run localised the loss
exactly:

| stage | domain rels |
|---|---|
| extracted | 11 |
| after_memory_shaping | 11 |
| after_ontology_context | 11 |
| after_identity_keys | 11 |
| **endpoints_resolvable** | **0** |

Root cause: when a model emits sequential node ids (`"1"`,`"2"`) plus names,
`_normalize_node` replaces the sequential id with the entity name (collision
avoidance), but `node_lookup` was never keyed by the original id, so a
relationship referencing `"2"` orphaned and the edge was dropped. Fix: key
`node_lookup` by the original raw id. **Live before/after on the same doc:
`endpoints_resolvable` 0 → 12, `RELATED_TO` persisted 0 → 12.** With that landed,
stage 1 is healthy (3/3 runs: 24 nodes, 17 domain edges) and the breakdown is
measured against a graph that has traversable structure.

## The breakdown (decision)

Sample: GraphRAG-Bench-style fact retrieval from *An Unsentimental Journey
through Cornwall*.

> Q: which plant known scientifically as **Erica vagans** is also referred to by
> another common name, and what is that name?  **Gold: Cornish heath**

Source text: *"the _erica vagans_ — the lovely **Cornish heath** …"* — an
appositive alias.

Per-stage attribution, 3 identical runs (fixed code, same doc/question):

| stage | verdict | evidence |
|---|---|---|
| **1. indexing / extraction** | ❌ **dominant miss** | `erica vagans` node created 3/3, but the appositive alias "Cornish heath" was **not** lifted into structure — no second node, no `ALSO_KNOWN_AS` edge, no alias property. `erica vagans` edges are only to Goonhilly Down / serpentine / magnesian earth. "Cornish heath" exists **only in `Chunk.text`** (1 chunk), unreachable by graph traversal. |
| **2. ontology** | ⚠️ contributes | the generic single-`Entity` + "verb-in-a-property" design has **no slot** for an appositive alias, so even a willing extractor has nowhere canonical to put "also known as". |
| **3. retrieval / text2cypher** | ❌ **real miss (see Correction)** | anchor-slot extraction non-deterministically picks the question's framing-clause book title ("An Unsentimental Journey through Cornwall") as the anchor entity, so the Cypher matches no node and returns 0 rows — faithfully. |
| **4. generation** | ✅ honest | refused to fabricate; the "erica vagans exists" phrasing echoes the question, not a retrieved node (retrieval returned 0). |

Result: **gold_hit 0/3.**

### Correction (post-review, 2026-08-17)

A follow-up diagnostic overturned two claims in the original write-up:

1. **`records=0` is faithful, not a reporting artifact.** The executor feeds the
   *same* `records` list to synthesis and to the metadata (`local_engine.py`
   ~964 vs ~987), so metadata cannot undercount what synthesis received. On the
   captured run the executed Cypher was anchored on the book title
   (`anchor="An Unsentimental Journey through Cornwall"`) and returned 0 rows;
   a direct re-exec of that exact Cypher+params also returned 0. The earlier
   "1 row on manual re-exec" came from a *different* run where the slot
   extractor happened to anchor on "Erica vagans" — i.e. the anchor selection
   is **non-deterministic**.
2. **The failure is not cleanly stage-1.** It spans **stage-1** (the appositive
   alias is never lifted into structure) *and* **stage-3** (anchor-slot
   mis-selection retrieves nothing). On the captured run stage-3 alone would
   fail even with a perfectly indexed alias. A single sample (n=1) with no
   ceiling/floor control cannot attribute the loss to one stage.

### Conclusion

For this sample the honest statement is narrow: **GraphRAG returned no answer
because the fact was not reliably in the graph (stage 1) AND the anchor was
mis-selected (stage 3) — non-deterministically.** This is *not* evidence that
alias fact-retrieval as a class breaks at stage 1; that claim needs a stratified
sample and ceiling/floor controls (see "Review caveats"). The lowest-complexity,
highest-frequency fix surfaced here is stage-3: strip question framing clauses
("In the narrative of 'X', …") before anchor-slot extraction.

Observability confirmed: the `rag.ask` root span nested the full stage tree
(`rag.schema → rag.compile_cypher → rag.execute → rag.retrieve_ctx →
rag.synthesize`), 17 spans emitted for one ask (ADR-0144).

## Consequences

- **Follow-up (extraction):** appositive/alias capture at stage 1 + an ontology
  alias slot. Prove with before/after `gold_hit` on this sample and broaden the
  base. (new ticket)
- **Follow-up (retrieval, highest-value / lowest-complexity):** anchor-slot
  extraction must strip question framing clauses ("In the narrative of 'X', …")
  and rank real-subject entity names over quoted titles, so it does not anchor
  on the book title. (new ticket)
- **Kept:** the relationship-survival census is a useful index-quality
  instrument — but see the counter caveat below before trusting its terminal
  numbers.

## Review caveats (multi-agent review, 2026-08-17)

Four independent reviews (indexing, retrieval, ontology, RAG-eval) landed
**ADJUST/REDIRECT**. What is solid vs not:

- **Solid:** #589 (endpoint remap) is correct and safe; the `endpoints_resolvable`
  0→12 before/after was read directly from the graph. Observability spine
  (ADR-0144) is a keeper.
- **Instrument caveat:** `graph_store.write` counts *submitted rows*, not edges
  actually created (no `.consume()` counter read), so `written_to_store` /
  `total_relationships` are inflated and provenance-inclusive — do not compare
  them to the domain-only earlier census stages.
- **Metric caveat:** `gold_hit` is a bare substring match, prone to false
  positives (refusal that name-drops the term) and false negatives (paraphrase).
- **Attribution caveat:** n=1, no ceiling/floor control; the SDK already exposes
  `query_no_graph_records` / `indexing_no_graph_writes` for exactly these
  controls. Any class-level claim needs a stratified sample first.

These are recorded as caveats, not new build work — deliberately kept minimal.

## Validation

- `bash scripts/ci/run_basic_ci.sh` (at #589): 1106 passed, 3 skipped; all
  contract checks green.
- Live e2e: MARA MiniMax-M2.7 + DozerDB 5.26.3, workspace-scoped, 3 runs.
- Raw per-run data: `ADR-0213-graphrag-stage-breakdown.json`.
