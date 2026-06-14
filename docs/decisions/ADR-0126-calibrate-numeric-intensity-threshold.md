# ADR-0126: Calibrate the Guardrail Selector's Numeric-Intensity Threshold from Measured Answer-Accuracy

Date: 2026-06-14
Status: Proposed

## Context

ADR-0123's `select_guardrail` uses a fixed `numeric_threshold=0.5` to decide
whether a corpus is "numeric" (→ lean guardrail) or "entity" (→ rich). ADR-0122/
0124 produced the ground-truth signal that boundary should track: per-domain
**answer-accuracy deltas** (rich minus sparse). The threshold should be learned
from those deltas, not guessed.

## Decision

Add to `seocho.guardrail_selector` (pure/offline/deterministic):

- `DomainObservation(domain, numeric_intensity, rich_minus_sparse_delta)` — a
  measured per-domain point.
- `calibrate_numeric_threshold(observations, *, default=0.5)` — finds the
  threshold T best separating domains where the rich guardrail helped
  (`delta>0` → entity, `ni<T`) from where it did not (`delta<=0` → numeric,
  `ni>=T`). Scans candidate thresholds (midpoints of sorted unique intensities +
  0/1 bounds), maximizes agreement with measured outcomes, ties broken toward
  `default`. Returns `{threshold, accuracy, n, default}`.

The calibrated T is passed to `select_guardrail(..., numeric_threshold=T)`.

## Validation

`tests/seocho/test_numeric_threshold_calibration.py` (4): cleanly separable
observations → accuracy 1.0 and the boundary lands between the entity and numeric
clusters; empty → default; noisy/overlapping → best-possible separator
(accuracy ≥ 0.75); the calibrated threshold flips the selector's entity/numeric
decision at the boundary. `run_basic_ci` green.

## Consequences

- The domain split is now data-driven: feed the ADR-0122/0124 per-category deltas
  (Governance +0.67 … Shareholder return −0.17) and their corpus numeric
  intensities to learn the operating threshold for a given corpus family.
- Follow-up: as more answer-accuracy measurements accrue (more domains, more
  judges), re-calibrate; consider a soft/probabilistic boundary instead of a hard
  cut.
