# ADR-0197: The ERB graph arm is built from our extraction, not from `answer_facts`

- Status: Accepted
- Date: 2026-08-16
- Related: `ADR-0181` (OS contract), `scripts/serve_track/erb_index.py`

## Context

The project decided that the ERB graph arm must come from SEOCHO's own indexing.
It is written down in `erb_index.py`:

> That version built the graph arm from the dataset's own `answer_facts` … it
> routes around the thing this project actually builds.

`erb_adapter.py` did it anyway, and was modified *later* than that decision. On
the committed run, `edge_source` was `answer_facts` for 76 of 79 items, and for
**20 of 20** `ERB_conflicting_info` items.

That matters more than a provenance slip, because `ERB_conflicting_info` is the
**only statistically significant graph-vs-vector comparison in the entire
programme** — graph 17/20 against vector 10/20, exact McNemar p = 0.0156. Every
other arm comparison, across three corpora and 41 tests, is null.

## What `answer_facts` actually contains

Not facts about the world. The dataset's decomposed grading claims:

    (The answer must state that the updated reservation target)
      -[IS]-> (30% of interactive burst credits ... on dp-132-usw)
    (The answer may mention that an earlier/internal suggestion)
      -[WAS]-> (20%, but it must clearly indicate 30% is the current target)

The graph arm was reading the rubric it was about to be graded against.

## Decision

`--edge-source extracted` is the default: triples come from
`outputs/serve_track/erb_extracted.jsonl`, joined to questions on
`expected_doc_ids`, with node ids resolved to names.

`--edge-source answer_facts` stays reachable. An oracle arm is a legitimate
**ceiling**, and having one is required by our own controls discipline. It now
labels itself `answer_facts_CEILING` in the output, so it cannot be read as a
graph result.

Running `--edge-source extracted` with no extracted graph is a hard error rather
than a silent fallback. Silently substituting a different edge source is how
this happened.

## Measured

20 items in the affected stratum, rubric detector
`the answer|must|should|may mention`:

| edge source | edges | rubric-phrased | share |
| --- | --- | --- | --- |
| `answer_facts` | 115 | 115 | **100%** |
| our extraction | 635 | 1 | **0%** |

Same question, the two arms side by side:

    answer_facts:  The answer must state that the updated reservation target
                     -IS-> 30% of interactive burst credits ...

    extracted:     Sustained 429s during sliced ingest ... -AFFECTS-> Streamly AI
                   Sustained 429s during sliced ingest ... -OCCURRED_ON-> dp-132-usw

The extracted graph covers 285 documents with 5,348 triples — 5.5× the edge
volume, and about entities and events rather than about what a marker should
accept.

## Consequences

- **The p = 0.0156 result must be re-run and, until then, not cited.** It was
  produced by an arm holding the answer key.
- Graph-arm coverage now depends on extraction quality, which is the point: the
  arm measures the component under test. 285 of 322 documents yielded triples;
  the 37 that did not are extraction failures and should be counted as such
  rather than papered over by a fallback.
- `edges_from_text` remains as a last-resort fallback for documents with no
  extraction, and still labels itself `document_text`.

## What this does not fix

The harness still has **no retrieval stage** — the graph arm is fed
`gold_edges` and the vector arm the gold documents, so both arms receive oracle
evidence and the comparison is between serialisation formats under held-perfect
retrieval. That is a separate and larger gap; this ADR only removes the answer
key from one side of it.
