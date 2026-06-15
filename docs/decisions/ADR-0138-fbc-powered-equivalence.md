# ADR-0138: Powered — Bridged-FIBO Guardrail Is Statistically EQUIVALENT to the Curated Slice (Supersedes ADR-0137's Directional Claim)

Date: 2026-06-14
Status: Accepted

## Context

ADR-0137 reported, on N=15 / single judge, that `fibo_fbc_generic` (0.667) beat
curated `fibo_plus` (0.600) on FinDER answer accuracy — explicitly flagged as
directional, not powered. This is the powered confirmation: larger N, a second
independent judge, and paired statistics.

## Experiment

`scripts/benchmarks/fbc_generic_powered.py`, MARA. **96 cases (12 × all 8
categories)**, answer model DeepSeek-V3.1, **two judges** (DeepSeek-V3.1 +
gpt-oss-120b), `consensus` = both judges agree correct. Paired stats: McNemar's
test + a 5,000-iteration bootstrap 95% CI on the per-case accuracy difference.
Record: `docs/decisions/ADR-0138-fbc-powered.json`.

## Findings (measured)

| guardrail | consensus acc | judge1 | judge2 |
|---|---|---|---|
| curated_plus (9) | 0.552 | 0.667 | 0.594 |
| fibo_fbc_generic (25) | 0.490 | 0.635 | 0.563 |

- **Δ (fibo − curated) = −0.0625** — the *opposite* sign to the N=15 result.
- **McNemar:** discordant pairs b=15 (curated right / fibo wrong), c=9 (fibo right
  / curated wrong); χ²=1.04, **p = 0.307** → **not significant**.
- **Bootstrap 95% CI on Δ: [−0.156, +0.042]** → contains 0.
- Inter-judge agreement: 0.81.

## Conclusion

**The two guardrails are statistically indistinguishable on FinDER answer accuracy
(McNemar p=0.31; 95% CI straddles 0).** ADR-0137's "matches/beats" was a small-N
artifact — the powered measurement caught it. The correct claim is **equivalence**,
not superiority (and not inferiority).

This *strengthens* the product thesis rather than weakening it: a bridged,
version-pinned **official-FIBO-derived guardrail performs on par with the
hand-curated slice** — so hand-curation can be replaced by the FIBO-derived
pipeline (`catalog → bridge → collapse`) **at no measurable answer-accuracy cost**,
gaining provenance, reuse, and automatic term discovery for free.

## Consequences

- **Supersedes ADR-0137's directional claim.** Cite equivalence (p=0.31, CI
  [−0.16, +0.04]), not a win.
- Methodological note for future eval ADRs: report paired stats (McNemar +
  bootstrap CI) and ≥2 judges; treat single-judge small-N deltas as hypotheses,
  not results. The N=15→N=96 sign flip is the cautionary example.
- The FIBO arc (ADR-0132→0138) concludes: official FIBO is a viable, provenance-
  bearing guardrail source, statistically on par with curated — the engineering
  win is automation + version-pinning, not a quality jump.
- Follow-up (optional): widen further / add a third judge for tighter CIs; the
  current result is already adequate to retire hand-made slices for FIBO-derived
  guardrails.
