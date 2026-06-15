# ADR-0139: Auto-Deriving the FIBO Root Seed — Viable, Variable; auto∪hand Is Robust

Date: 2026-06-14
Status: Proposed

## Context

ADR-0136/0138 left one piece of manual curation: the 5-entry `FINDER_FIBO_ROOTS`
seed (generic term → FIBO root). This automates it: an LLM maps each generic
corpus term to FIBO root classes, replacing the hand seed.

## Decision

Add `seocho.fibo_catalog`:
- `root_candidates(ontology, top)` — the most root-like classes (highest
  subClassOf-descendant count) + definitions, the propagation anchors.
- `derive_fibo_roots(generic_terms, ontology, *, backend)` — LLM (injected, via
  the structured layer) maps each generic term → FIBO roots it subsumes; roots
  validated to exist. Fake-testable.
- `auto_semantic_bridge(ontology, generic_terms, *, backend)` — derive + bridge,
  the no-hand-seed form of the ADR-0136 pipeline.

## Validation (measured, real FIBO @fee10a4, FinDER corpus, MARA DeepSeek-V3.1)

`docs/decisions/ADR-0139-auto-roots.json`. corpus_coverage after each seed:

| module | lexical | hand seed | **auto seed** | **auto ∪ hand** |
|---|---|---|---|---|
| BE | 0.181 | 0.181 | 0.192 | 0.192 |
| FBC | 0.316 | 0.609 | 0.340 | **0.633** |
| FND | 0.300 | 0.594 | **0.862** | 0.862 |
| SEC | 0.192 | 0.485 | 0.280 | **0.573** |

(curated_plus reference = 0.595.)

- **Auto-derivation is viable and sometimes far better** — FND **0.862** (≫ hand
  0.594 and curated 0.595); BE slightly above hand. The LLM found broader/better
  FND roots than the hand seed.
- **But it's module-variable** — FBC 0.340 and SEC 0.280 fall *below* the hand
  seed, because the LLM under-mapped the high-mass `FinancialMetric` label there
  (it picked price/instrument leaves, not the Security/Share roots the hand seed
  forced).
- **`auto ∪ hand` is robustly best** — ≥ max(auto, hand) for every module, and
  exceeds curated on FBC (0.633) and FND (0.862). The union takes the LLM's
  discoveries *plus* the hand seed's safety-net mappings.

## Decision / recommendation

- **Default to auto-derivation, unioned with a tiny curated fallback seed.** This
  reduces manual curation to a small safety net (the `FinancialMetric`→Security/
  Share mapping) while the LLM contributes the rest — robustly ≥ both alone.
- `derive_fibo_roots` is the automation; `FINDER_FIBO_ROOTS` becomes the fallback,
  not the primary.

## Honest caveats

- Single-model derivation; quality varies by module (FBC/SEC under hand). A
  multi-model vote or a second pass focused on the highest-mass uncovered corpus
  labels would likely stabilize it (follow-up).
- corpus_coverage is the proxy; the answer-accuracy equivalence (ADR-0138) was
  measured on the curated-equivalent generic guardrail, not yet on an auto∪hand
  bridged module — a powered answer re-run on the union is the natural next check.

## Consequences

- The FIBO-derived guardrail pipeline can now run with **near-zero hand curation**
  (auto-derive + small fallback), version-pinned to the FIBO commit.
- Follow-ups: multi-model/2-pass derivation for consistency; wire auto∪hand into
  the guardrail selector; powered answer re-run on a union-bridged module.
