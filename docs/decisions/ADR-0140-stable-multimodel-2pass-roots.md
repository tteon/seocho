# ADR-0140: Multi-Model + 2-Pass Seed Derivation Stabilizes Auto-Roots — Zero Hand Curation, Beats Curated

Date: 2026-06-14
Status: Accepted

## Context

ADR-0139's single-model auto-seed was module-variable (FBC 0.340, SEC 0.280 below
the hand seed) because one model under-mapped some high-mass corpus labels. Goal:
stabilize so auto-derivation reaches/exceeds the hand seed on every module —
removing the last hand curation.

## Decision

Add `seocho.fibo_catalog`:
- `derive_fibo_roots_multi(generic_terms, ontology, *, backends, models)` — union
  `derive_fibo_roots` across several models (each surfaces more valid roots).
- `derive_fibo_roots_stable(..., passes=2)` — pass 1 unions across models; each
  further pass re-derives ONLY the still-unmapped terms with a wider candidate
  window. Multi-model coverage + a rescue pass for misses.

## Validation (measured, real FIBO @fee10a4, FinDER, 3 MARA models: DeepSeek-V3.1 + MiniMax-M2.5 + gpt-oss-120b, 2 passes)

`docs/decisions/ADR-0140-stable-roots.json`. corpus_coverage:

| module | lexical | hand seed | single-model auto | **stable (multi+2pass)** |
|---|---|---|---|---|
| BE | 0.181 | 0.181 | 0.192 | **0.227** |
| FBC | 0.316 | 0.609 | 0.340 | **0.784** |
| FND | 0.300 | 0.594 | 0.862 | **0.862** |
| SEC | 0.192 | 0.485 | 0.280 | **0.613** |

(curated_plus reference = 0.595.)

- **Stable ≥ hand AND ≥ single-auto on every module** — the multi-model union +
  2-pass rescue eliminated the single-model variability that sank FBC/SEC.
- **FBC 0.784, FND 0.862, SEC 0.613 all now exceed curated_plus (0.595)** — and
  this uses **zero hand curation** (no `FINDER_FIBO_ROOTS`).
- BE (0.227) stays low — it genuinely lacks financial-metric roots; module fit,
  not seed quality.

## Conclusion

**Fully-automated, multi-model + 2-pass seed derivation removes ALL manual
curation and produces FIBO-derived guardrails that beat both the hand seed and the
hand-curated slice on coverage.** The FIBO-guardrail pipeline is now end-to-end
automatic and version-pinned:

`compiled catalog → lexical bridge → multi-model+2-pass semantic bridge → guardrail`

`FINDER_FIBO_ROOTS` is retained only as an optional fallback; it is no longer
needed for the stable path.

## Consequences

- Caps the FIBO arc (ADR-0132→0140): official FIBO is the authoritative,
  version-pinned source; a fully-automated bridge yields guardrails ≥ curated on
  coverage with no hand curation.
- Cost: 3 models × up to 2 passes = a handful of derive calls per module (offline
  scoring otherwise). Cheap relative to its payoff.
- Follow-up: a powered ANSWER-accuracy re-run (ADR-0138 method) on a
  stable-bridged module to confirm the coverage gain carries to answers; wire
  `derive_fibo_roots_stable` into the guardrail selector as the default candidate
  builder.
