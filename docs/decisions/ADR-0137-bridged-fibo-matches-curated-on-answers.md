# ADR-0137: A Bridged-FIBO-Derived Guardrail Matches/Beats the Curated Slice on FinDER Answers

Date: 2026-06-14
Status: Proposed

## Context

ADR-0136 showed lexical+semantic bridging lifts official FIBO's *coverage* to/above
curated (`FBC` 0.609 vs `fibo_plus` 0.595). The remaining question: does that
translate to **answer accuracy**? Raw FBC (515 classes) is too large to inject as
an extraction prompt (ADR-0134), so we collapse the bridged FBC to its distinct
corpus-relevant **generic vocabulary** (25 terms) — a small, prompt-sized,
version-pinned guardrail derived from official FIBO — and compare to the
hand-curated slice.

## Experiment

`scripts/benchmarks/fbc_generic_answer.py`, MARA DeepSeek-V3.1, 15 FinDER cases
(5 × Company overview / Financials / Governance). Arm A = curated `fibo_plus`
(9 classes); Arm B = `fibo_fbc_generic` (25 generic terms the bridged FBC surfaced,
version `fee10a4`). 0 errors. Record: `docs/decisions/ADR-0137-fbc-generic-answer.json`.

## Findings (measured)

| guardrail | answer accuracy | Company overview | Financials | Governance |
|---|---|---|---|---|
| curated_plus (9) | 0.600 | 0.6 | 0.2 | 1.0 |
| **fibo_fbc_generic (25, FIBO @fee10a4)** | **0.667** | 0.6 | **0.4** | 1.0 |

The **FIBO-derived generic guardrail slightly outperforms the hand-curated slice**
(+0.067 overall), driven by Financials (0.2→0.4) — the extra generic types FBC
surfaced (Market, Insurance, Service, FinancialInstrument, …) gave the model more
adequate labels for financial questions, while staying prompt-sized.

## Interpretation & decision

- **Capstone of the FIBO arc (ADR-0132→0137):** official, version-pinned FIBO,
  lexically + semantically bridged and collapsed to its corpus-relevant generic
  vocabulary, is a **viable guardrail that matches/beats the hand-made slice** on
  answer accuracy — while carrying the FIBO commit/hash as provenance.
- Therefore the hand-made `examples/datasets/fibo_*.jsonld` can be **retired** in
  favor of FIBO-derived guardrails: `catalog → bridge (lexical+semantic, 5-entry
  seed) → collapse to generic vocabulary → guardrail`.
- The pipeline that produces it is offline/deterministic except the corpus profile
  (one open extraction) and the answer eval (MARA).

## Honest caveats

- **N=15, single model, LLM-judge** — directional, not a powered comparison. Widen
  N + add a second judge before an external claim of superiority; the safe claim is
  **parity-or-better**, not a robust win.
- The generic guardrail still uses the `FINDER_FIBO_ROOTS` seed (small domain
  curation) + the corpus profile to choose terms — so it is "FIBO + small seed +
  corpus", not zero-curation. The gain over hand-curation is provenance + reuse +
  automatic term discovery, not the elimination of all curation.

## Consequences

- Closes the loop the user cares about (finance, numbers/vocab fidelity): the
  guardrail is now sourced from official FIBO with provenance, and measured to be
  at least as good as the hand-made one for answering.
- Follow-ups: widen N / second judge for a powered result; shrink the seed via FIBO
  `same_as`/skos; wire `build_fbc_generic` into the guardrail selector as a
  first-class candidate.
