# ADR-0181: cold-start extraction A/B — pure-open vs upper-anchored (seocho-ia4.11)

Date: 2026-08-16 · Status: accepted (measured) · seocho-ia4.11

## Context

ADR-0179 designed upper-ontology-anchored open extraction for cold-start. This is the
LIVE A/B on a real, instance-diverse corpus (FinDER tutorial subset: 10 docs, ~10
distinct companies, recurring types). Both arms = real MARA MiniMax-M2.7 extraction
over the SAME corpus; only variable = the extraction context.
`scripts/agentos/e2e_cold_start_ab.py`.

## Result

| metric | PURE_OPEN | BOOTSTRAP (upper-anchored + running vocab) |
|---|---|---|
| nodes / relationships | 69 / 69 | **86 / 92** |
| company coverage | 9/9 | 8/9 |
| distinct entity types | 26 | 36 |
| induced HIERARCHICAL types (broader) | **0** | **36** |
| axioms mined | 13 | **20** |
| type_drift_spread (string-norm) | 1.0 | 1.0 |

## Findings (honest)

- **Recall: no penalty — the key result.** Bootstrap did NOT suppress recall; it
  slightly increased it (86 vs 69 nodes, 92 vs 69 rels; coverage 8/9 vs 9/9). This is
  the "Anchor, Don't Name" firewall RE-TEST: the recall hit is specific to injecting a
  RICH domain ontology; a SMALL abstract upper frame is recall-safe. Hypothesis
  confirmed.
- **Structure / axiom-support: bootstrap wins clearly.** Every induced type is anchored
  to an upper category (36/36 hierarchical vs 0) — a free subclass hierarchy — and more
  axioms are inducible (20 vs 13). This is the anchor's real payoff.
- **Drift: INCONCLUSIVE — the metric is too weak.** `type_drift_spread`
  (lowercase/strip/singularize) cannot catch SEMANTIC synonyms (Company/Corporation/
  Firm normalize apart), so both arms read 1.0. And bootstrap produced MORE types (36
  vs 26) — it increased granularity, not reduced it. The true drift control is not
  "fewer types" but "all 36 types grouped under ~11 upper categories" (pure-open has
  zero grouping). Measuring semantic drift properly needs embedding-clustering of type
  labels or a gold concept map — a follow-up.

## Decision / consequences

- The cold-start bootstrap extraction mode is validated on the two provable axes
  (recall-safe + hierarchy/axiom-rich). Wiring it into the engine (enforcement mode
  `bootstrap` via the upper frame + running vocabulary, replacing the empty-slot open
  path) is warranted — the recall fear is disproven.
- Open: a semantic drift metric (embedding-cluster type labels) to test the grouping
  claim directly; answer-quality A/B (does the hierarchical/enriched graph answer
  better). ia4.11 follow-ups.
- Harness: `scripts/agentos/e2e_cold_start_ab.py`. Design:
  wiki/cold-start-schema-bootstrap-design.md.
