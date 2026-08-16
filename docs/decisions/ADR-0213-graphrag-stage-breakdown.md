# ADR-0213: Graph agentic-RAG stage breakdown — where end-to-end quality is lost

- Status: accepted
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
| **3. retrieval / text2cypher** | ❌ secondary miss | intent classified as `entity_lookup` (`relationship_type=""`, `target_entity=""`); the generated Cypher fetches the `erica vagans` node and does **not** traverse an alias relationship. |
| **4. generation** | ✅ honest | the synthesizer refused to fabricate: *"erica vagans exists … but I cannot fully answer."* No hallucination — the desired failure mode. |

Result: **gold_hit 0/3.** The gold answer is structurally absent from the graph,
so no retrieval or generation quality could recover it.

### Conclusion

For appositive/alias fact-retrieval, GraphRAG breaks at **stage 1 (extraction
coverage)**, not at retrieval or generation. The highest-leverage improvement is
extraction-side alias capture (appositive `X — the Y` → `ALSO_KNOWN_AS` edge or
`alias` property), paired with an ontology slot to hold it — **not** a retrieval
fix. This is the kind of attribution the breakdown exists to produce: it moves
the work to the stage that actually loses the answer.

Observability confirmed: the `rag.ask` root span nested the full stage tree
(`rag.schema → rag.compile_cypher → rag.execute → rag.retrieve_ctx →
rag.synthesize`), 17 spans emitted for one ask (ADR-0144).

## Consequences

- **Follow-up (extraction):** appositive/alias capture at stage 1 + an ontology
  alias slot. Prove with before/after `gold_hit` on this sample and broaden the
  base. (new ticket)
- **Follow-up (retrieval):** intent classifier should route "also known
  as / another name for" as an alias/relationship traversal, not `entity_lookup`.
  (new ticket)
- **Bug (reporting artifact):** `_LocalEngine._last_query_metadata["records"]`
  read 0 while the executed Cypher returned 1 row on manual re-exec (the
  synthesizer did see the node) — the metadata undercounts retrieved rows.
  Diagnostics only; does not change answers. (new ticket)
- **Kept:** the relationship-survival census is a permanent index-quality
  instrument; any future "graph looks empty" regression is one live run from a
  stage-localised diagnosis.

## Validation

- `bash scripts/ci/run_basic_ci.sh` (at #589): 1106 passed, 3 skipped; all
  contract checks green.
- Live e2e: MARA MiniMax-M2.7 + DozerDB 5.26.3, workspace-scoped, 3 runs.
- Raw per-run data: `ADR-0213-graphrag-stage-breakdown.json`.
