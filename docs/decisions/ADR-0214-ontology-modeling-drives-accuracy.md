# ADR-0214: Ontology modeling drives graph-RAG accuracy — and can be demand-proposed

- Status: experimental
- Date: 2026-08-17
- Tickets: seocho-5ny (breakdown epic)
- Related: ADR-0213 (stage breakdown, records=0), #592 (anchor de-framing),
  #593 (real write counters), ADR-0114 (ontology scorecard),
  seocho-5bg / #493 (ontology import), #279 (`seocho run` YAML)

## Context

ADR-0213 localized a graph-RAG failure to a mix of stage-1 (extraction coverage)
and stage-3 (anchor mis-selection), but ran on a **generic single-`Entity`
ontology** — which the dbt "Semantic Layer vs Text-to-SQL 2026" benchmark would
call *modeling turned off*. dbt's headline finding: deterministic query
generation over a **well-modeled** ontology hits ~100% and fails loud, while
text-to-SQL fails silently; and *"data modeling quality matters enormously."*

SEOCHO's default query path (`DeterministicQueryPlanner`) is the Semantic-Layer
analog: the LLM decomposes the question into typed slots, and the planner builds
the Cypher deterministically. So the open question was: **are we using the
ontology well, and does modeling move SEOCHO's accuracy the way dbt predicts?**

Two enabling fixes landed first: **#592** (strip source-framing clauses before
anchor-slot extraction — closes the ADR-0213 anchor bug) and **#593** (write()
reads real Neo4j counters, not submitted rows — makes the census terminal stage
trustworthy).

## Experiment

Live on MARA MiniMax-M2.7 + DozerDB 5.26.3. One document (a Cornwall travelogue),
**8 stratified questions** with verifiable golds (alias, term-meaning, entity-
attribute, lookup, relationship). Accuracy graded by a **MARA judge** into
`correct / refused / silent_wrong` (not a bare substring, per ADR-0213 caveat).

### Axis 1 — ontology modeling (deterministic path)

| ontology | correct | refused | silent_wrong | gold fact in structure |
|---|---|---|---|---|
| **GENERIC** (single `Entity` + `RELATED_TO`) | **1/8** | 7/8 | **0** | ❌ |
| **MODELED** (typed: Plant/Place/Person/Animal/Term + typed rels) | **5/8** | 3/8 | **0** | ✅ |

- Extraction side: GENERIC wrote 17 nodes / 11 domain edges, all labeled `Entity`;
  MODELED wrote 20 nodes / 6 domain edges across typed labels, and lifted the
  alias "Cornish heath" into structure. Fewer domain edges but the *answer-bearing*
  facts landed in typed slots — the extraction/query tension is real (rich
  ontology aids query, changes extraction shape).
- **silent_wrong = 0 in both.** SEOCHO fails loud (refuses) rather than
  confidently wrong — the property dbt names as decisive in production.

### Control — ceiling

Inject the `erica vagans →(ALSO_KNOWN_AS)→ Cornish heath` alias into the MODELED
graph and re-ask the alias question → **correct**. Proves the query+generation
path recovers the answer when the fact is present; the earlier failures were
upstream (extraction/modeling), not retrieval/generation defects.

### Axis 2 — can the modeled ontology be *proposed* from the questions?

A prototype `propose_ontology(doc, questions)` asks an LLM to draft the **minimal
typed ontology** that covers the questions (draft-never-persists), then the same
coverage loop grades it:

| ontology source | correct | note |
|---|---|---|
| GENERIC (no modeling) | 1/8 | baseline |
| **proposed v1** (zero hand-authoring) | **3/8** | some facts modeled as properties on broad entities → extraction didn't fill them |
| **proposed v2** (prompt prefers *dedicated typed nodes* for answer-bearing facts) | **4/8** | e.g. a `Breed` node fixed the ponies question |
| hand-MODELED | 5/8 | last point is extraction non-determinism, not modeling |

The loop **moves the bottleneck**: 1/8 (no slots) → 3/8 (slots, some mis-placed)
→ 4/8 (dedicated typed nodes) — after which the residual refusals are **stage-1
extraction-coverage** misses (the typed slot exists but the hosted model didn't
populate it), not modeling gaps. That is exactly the signal a coverage-feedback
loop should surface to the user for the next iteration.

## Decision / product direction

1. **Ontology modeling is the highest-leverage accuracy lever on the query
   side** (1/8 → 5/8 here), and SEOCHO run with a generic ontology is *not* using
   the ontology. Guide users to typed modeling.
2. **Guide it demand-first, not blank-editor-first.** A blank schema editor
   reproduces GENERIC (users under-model). The natural UX is a loop:
   `documents + target questions → propose_ontology draft → review/edit (arrows
   import / YAML run-spec / CLI) → index + coverage + scorecard feedback →
   iterate`. Every proposed slot exists because a question needs it, which also
   keeps the ontology minimal — a natural regularizer against the extraction
   tension.
3. **Design rule for the proposer (measured):** model an answer-bearing fact as
   its **own typed node** (Breed, Term, Person), not a free-text property on a
   broad entity — dedicated nodes get populated by extraction reliably; broad-
   entity string slots often don't.

Building blocks that already exist: `run_spec.py` (YAML ontology), `ontology_import.py`
(arrows/cypher import), `ontology_scorecard.py` (`score_ontology`), `cli/ontology.py`.
The one new piece is the demand-driven `propose_ontology` + the coverage-feedback
surface (prototyped here, not yet productized).

## Review caveats (carried from ADR-0213, still binding)

- **n is small:** 1 document, 8 questions, mostly single runs — indexing is
  non-deterministic on the hosted model, so per-cell counts carry ±1–2 noise.
  Not yet a class-level claim; a stratified ≥20-question set with repeats + CIs
  is needed to publish the magnitudes.
- `gold_hit` substring was replaced by a MARA judge here, but the judge itself is
  unaudited; spot-checked against substring, they agreed on this set.
- Only the deterministic path was run; the text2cypher arm (dbt's Text-to-SQL
  analog) is designed (`HybridQueryPlanner` + `SEOCHO_QUERY_PRECEDENCE=generated_first`)
  but not yet executed.

## Consequences / follow-ups

- **Productize `propose_ontology(docs, questions)`** as the guided entry point
  (draft-never-persists, review checklist), wired to the coverage + scorecard
  feedback. (new ticket)
- **Coverage-feedback surface:** turn refused-question → suggested-missing-slot
  into a user-facing report. (new ticket)
- **Broaden the benchmark** to ≥20 stratified questions across ≥3 documents with
  repeats, and add the text2cypher arm, before publishing magnitudes. (new ticket)
- Data: `ADR-0214-ontology-modeling.json`.
