# ADR-0176: bounded-staleness freshness policy — strict, but not stale (seocho-ia4.6)

Date: 2026-08-16 · Status: accepted (policy + frontier demonstration) · seocho-ia4.6

## Context

ia4.1 (ADR-0175) made ontology drift a *binary* barrier: any version mismatch →
block. Unconditional strictness fails in the opposite direction — it **over-refuses**.
Most drift is benign: the changed part of the ontology doesn't touch the subgraph a
query reads, or the data is one *compatible* version behind and reconcilable on read.
Conversely "warn" (serve everything) **under-refuses** — it answers against data the
drift has invalidated. hadry's thesis: using the ontology as a guardrail, "strict" is
only safe when recency AND relevance are judged; strict-but-stale enforces a dead
contract.

## Decision

`src/seocho/ontology/freshness.py::evaluate_freshness` — a **bounded-staleness** policy.
Staleness is a distance, not a boolean: `version_distance × drift_relevance`, gated by
`stamp_coverage` (can we even tell?) and `version_age`. Decision:
- no mismatch → **serve** (fresh);
- coverage below floor → **refuse** (blind, can't verify);
- drift irrelevant to the query's labels → **serve**;
- contract older than `max_age_days` → **refuse**;
- relevant drift within `max_version_distance` → **repair** (reconcile on read, served);
- relevant drift beyond the bound → **refuse**.
`freshness_to_drift_policy()` bridges to the ia4.1 barrier (refuse→'block').

## Result — refusal-ROC ablation (freshness beats both fixed corners)

`scripts/agentos/ablation_freshness_roc.py`: a deterministic population of post-bump
reads; ground truth `correct_if_served = (not relevant) or (distance ≤ H)` for a true
reconcilable horizon H=2. Metrics: under-refusal (served-but-wrong / harmful),
over-refusal (refused-but-correct / correct).

| policy | under-refusal | over-refusal |
|---|---|---|
| always_warn (serve all) | **100%** | 0% |
| always_block (ia4.1 unconditional strict) | 0% | **100%** |
| **freshness (bound = H)** | **0%** | **0%** |

The two fixed policies structurally pin one error type to its maximum; the
bounded-staleness policy achieves ~0 on **both** — it dominates the two corners. The
bound sweep traces the ROC frontier and degrades gracefully when mis-tuned:

| bound b | under | over |
|---|---|---|
| 0 (≈block) | 0% | 29% |
| 1 | 0% | 14% |
| **2 (=H)** | **0%** | **0%** |
| 3 | 33% | 0% |
| 5 (≈warn) | 100% | 0% |

## Consequences

- The OS-necessity argument made measurable: a governed contract needs a freshness
  *bound*, not just strictness. Long-Horizon/Self-Evolving + Trust/Safety paper tracks.
- **Honest scope:** this is a *mechanism-frontier demonstration* on a synthetic ground
  truth — it proves the policy can *separate* the two error types the fixed policies
  cannot, and quantifies the cost of mis-tuning the bound. It is NOT a live
  measurement: the real payoff is matching the bound `b` to the true reconcilable
  horizon, which needs the compatibility classifier (ia4.2, BACKWARD/FORWARD/FULL) to
  supply per-change relevance/horizon, and the version chain (ia4.3) to supply
  `version_distance`. Wiring `evaluate_freshness` into the ia4.1 query-path barrier
  with those real signals is the follow-up.
- 7 unit tests; new standalone module (0 regressions).
