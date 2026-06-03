# ADR-0101: Scored Ontology Grounding (icml fibo_ground port)

Date: 2026-06-03
Status: Proposed

## Context

F8 (ADR-0100) showed the FinDER bottleneck is extraction/ontology-fit:
most queries return 0 records because the lane picks the wrong ontology
relationship. `CypherBuilder._match_relationship` resolves an
LLM-emitted relationship term by (1) exact/alias match, then (2) a blind
"first relationship whose source label matches" fallback, then (3)
single-relationship last resort. Step 2 is semantically blind: for an
ontology where several relationships share a source label, every
unmatched term collapses to the first one.

Measured (synonym-resolution A/B, ontology with LED_BY /
HEADQUARTERED_IN / HAS_SUBSIDIARY all sourced on Company):

| term          | OFF → resolved | correct? |
|---------------|----------------|----------|
| led           | LED_BY         | ✓        |
| headquartered | LED_BY         | ✗        |
| subsidiary    | LED_BY         | ✗        |
| location      | LED_BY         | ✗        |
| leadership    | LED_BY         | ✓        |

2/5 correct — the fallback picks LED_BY for everything.

The icml2026 `fibo_ground_node_label` / `fibo_ground_edge_type` traces
ground an NL intent to ontology types by **scored** similarity, returning
ranked `(type, score)` above a threshold (`audit committee` →
`[("hasCommittee",0.62),("HAS_COMMITTEE",0.55),("OVERSEES",0.41)]`).

## Decision

Port scored grounding as `seocho/query/ontology_grounding.py` and insert
it into `_match_relationship` as step 1.5 (after exact/alias, before the
blind fallback), opt-in via `SEOCHO_ONTOLOGY_GROUNDING` (default-off
pending wider validation).

- `ground(intent, candidates, top_k, threshold, scorer)` → ranked
  `(name, score)` above threshold, deterministic tie-break.
- `ground_edge_type` / `ground_node_label` pull candidates from the
  ontology (relationship names + aliases + same_as, collapsed to
  canonical; node labels).
- Default scorer is **lexical** (camelCase/snake-aware tokenization +
  content-token weighted Jaccard + containment bonus). The original used
  embedding cosine; SEOCHO has no live embedding backend (OpenAI key
  invalid, MARA serves none), so the scorer is **pluggable** (`scorer=`)
  — an embedding scorer drops in later with no caller change. The ported
  contract is "ranked, threshold-gated grounding", not the specific
  metric.
- `_grounded_relationship` only returns a type that clears the threshold
  **and** is label-compatible; otherwise "" → caller falls through to the
  existing fallbacks. Default-off and threshold-gated ⇒ additive, no
  regression to existing tests.

## Consequences

Positive — measured win (grounding ON, same A/B):

| term          | ON → resolved      | correct? |
|---------------|--------------------|----------|
| led           | LED_BY             | ✓        |
| headquartered | HEADQUARTERED_IN   | ✓        |
| subsidiary    | HAS_SUBSIDIARY     | ✓        |
| location      | LED_BY             | ✗        |
| leadership    | LED_BY             | ✓        |

4/5 correct (was 2/5). Grounding converts the blind fallback into
semantic disambiguation, fixing the two terms that previously collapsed
to LED_BY. This is the third opik-derived leg and, with AnswerShape, the
second to show a measured improvement (vs the RouteProfile/F8 nulls) —
because it targets the actual ontology-fit bottleneck F8 identified.

Tradeoffs / limits:
- The one remaining miss (`location` → no lexical overlap with
  `HEADQUARTERED_IN`) is the lexical scorer's ceiling; an embedding
  scorer would close it. The pluggable interface makes that a drop-in
  upgrade once a live embedding backend exists.
- Default-off until a FinDER-wide e2e A/B; grounding only affects
  relationship_lookup intents, so its lane-wide effect is bounded (most
  FinDER queries are metric/entity lookups that bypass
  `_match_relationship`).

## Implementation Notes

- new: `src/seocho/query/ontology_grounding.py`
- touched: `src/seocho/query/cypher_builder.py` (`_match_relationship`
  step 1.5 + `_grounded_relationship` + `_ontology_grounding_enabled`)
- tests: `tests/seocho/test_ontology_grounding.py` (9) — tokenizer,
  lexical scorer (committee example), threshold/top_k, edge/label
  grounding, alias→canonical collapse.
- env switch: `SEOCHO_ONTOLOGY_GROUNDING` (default-off).
- relates to: ADR-0100 (F8 surfaced the ontology-fit bottleneck this
  closes), ADR-0099 (AnswerShape — the other measured-win leg),
  ADR-0097 (cost model — grounding scores could later feed plan ranking).
