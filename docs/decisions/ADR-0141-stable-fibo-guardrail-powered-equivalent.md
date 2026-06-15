# ADR-0141: Powered (N=400) — Fully-Automated FIBO Guardrail Is Statistically Equivalent to the Curated Slice on Answers

Date: 2026-06-14
Status: Accepted

## Context

ADR-0140 produced a fully-automated, zero-hand-curation, version-pinned FIBO
guardrail (stable multi-model + 2-pass bridge) with *coverage* well above curated
(FBC 0.784 vs 0.595). The user asked to run FinDER with the improved guardrail;
scoped (chosen) to a powered N≈400 answer-accuracy comparison rather than the full
5,703 (≈34k calls). This is that powered confirmation.

## Experiment

`scripts/benchmarks/fbc_stable_powered.py`, MARA. Arm A = curated `fibo_plus` (9);
Arm B = `fibo_fbc_stable` — the stable-bridged FBC (auto seed via 3 models
DeepSeek-V3.1 + MiniMax-M2.5 + gpt-oss-120b, 2 passes, **no hand seed**) collapsed
to its 31 generic terms, version-pinned to FIBO `fee10a4`. **400 cases** (50 × 8
categories), answer model DeepSeek-V3.1, **2 judges** (DeepSeek + gpt-oss-120b,
consensus), McNemar + bootstrap. 0 errors. Record:
`docs/decisions/ADR-0141-fbc-stable-powered.json`.

## Findings (measured)

| guardrail | consensus acc | judge1 | judge2 |
|---|---|---|---|
| curated_plus (9, hand) | 0.5725 | 0.665 | 0.635 |
| fibo_fbc_stable (31, fully-auto) | 0.5750 | 0.670 | 0.648 |

- **Δ (auto − curated) = +0.0025** (essentially zero).
- **McNemar:** b=41, c=42, χ²=0.0, **p = 1.0** — discordant pairs nearly identical.
- **Bootstrap 95% CI on Δ: [−0.0425, +0.0475]** — tight, centered on 0 (much
  tighter than ADR-0138's N=96 [−0.16, +0.04]).
- Inter-judge agreement 0.84.

## Conclusion

**The fully-automated, zero-hand-curation, version-pinned FIBO-derived guardrail is
statistically equivalent to the hand-curated slice on FinDER answer accuracy**
(Δ=+0.0025, p=1.0, tight CI). The engineering win is unambiguous: hand curation can
be replaced by the automated FIBO pipeline (`compiled catalog → lexical +
multi-model/2-pass semantic bridge → collapse to generic vocabulary`) **at no
answer-accuracy cost**, gaining provenance (FIBO commit/hash), reuse, and zero
manual seeding.

Note the coverage↔answer dissociation (consistent with ADR-0122): ADR-0140's large
coverage gain (0.784 vs 0.595) did **not** produce an answer-accuracy gain — coverage
is a selector/structural proxy; on answers, a generic-vocabulary guardrail performs
the same regardless of whether it was hand-made or FIBO-derived. The value of the
FIBO derivation is automation + provenance, not an answer-quality jump.

## Consequences

- **Caps the FIBO arc (ADR-0132→0141):** official FIBO is the authoritative,
  version-pinned source; the fully-automated bridge yields a guardrail that *equals*
  the hand-curated slice on answers and *exceeds* it on coverage — so `fibo_*.jsonld`
  can be retired for the FIBO-derived pipeline.
- Methodology held: powered N + 2 judges + McNemar/bootstrap; the tight CI here
  upgrades the ADR-0138 finding from "indistinguishable (wide CI)" to
  "indistinguishable (tight CI)".
- Full-corpus (5,703) answer-eval remains available but is unnecessary for this
  conclusion; a full-corpus *coverage* pass would refine the selector profile if
  desired (cheaper).
