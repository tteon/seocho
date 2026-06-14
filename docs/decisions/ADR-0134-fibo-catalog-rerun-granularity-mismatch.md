# ADR-0134: FinDER Re-run on Real Compiled FIBO — Upstream Granularity Mismatches LLM Vocabulary

Date: 2026-06-14
Status: Proposed

## Context

ADR-0132/0133 brought the official FIBO upstream (pinned submodule) + a compiled
catalog + a loader that turns catalog modules into guardrail-candidate ontologies.
We re-ran the FinDER guardrail decision against the **real compiled FIBO** (not
hand-made slices) to see whether official FIBO modules beat the curated slices.

## Experiment

- **Compiled the real FIBO** (`git submodule update --init --depth 1
  third_party/fibo` @ `fee10a4...` → `scripts/ontology/compile_fibo_snapshot.py`):
  13,958 resources, modules **BE/FBC/FND/SEC**, snapshot hash `a5fbf8e4f50fe0df`,
  40 curated labels matched.
- Built guardrail candidates via `fibo_catalog.fibo_guardrail_candidates` (BE 193,
  FBC 515, FND 437, SEC 795 classes) + the curated `fibo_minus`/`fibo_plus`, and
  ran the corpus-aware selector on the FinDER open-extraction corpus profile.
  Record: `docs/decisions/ADR-0134-fibo-selector.json`.

## Findings (measured, version-pinned)

| candidate | corpus_coverage | classes |
|---|---|---|
| **curated_plus** (chosen) | **0.595** | 9 |
| curated_minus | 0.349 | 2 |
| FND (official) | 0.199 | 437 |
| FBC (official) | 0.093 | 515 |
| BE (official) | 0.008 | 193 |
| SEC (official) | 0.005 | 795 |

**The selector picks the curated 9-class slice over every official FIBO module —
and bigger modules score WORSE.** Root cause: official FIBO uses fine-grained,
formal labels (`JointStockCompany`, `BoardAgreement`, `RegistrationIdentifier`…)
that **do not match the LLM's generic open-extraction vocabulary** (`Company`,
`Person`, `FinancialMetric`, `Risk`). corpus_coverage matches corpus labels
against ontology labels/aliases, so a 795-class module whose labels never surface
in LLM output covers almost nothing.

A downstream answer-accuracy re-run (curated_plus vs official FND as the
extraction guardrail) was attempted but proved **impractical**: injecting FND's
437 entity types into the extraction prompt made calls huge and rate-limited
(>12 min for ~60 calls, unfinished) — itself evidence that a raw FIBO module is
not a viable extraction-prompt guardrail as-is.

## Interpretation

- **Bigger/official ≠ better guardrail.** Upstream FIBO granularity is the wrong
  shape for an LLM extraction guardrail; the curated coarse slices exist precisely
  to bridge that gap, and the corpus-aware selector correctly prefers them.
- This validates the curated-slice approach AND the selector against the real
  upstream, and explains why ADR-0132 ships a `compatibility_report.json`
  (official vs curated) rather than swapping curated for raw.

## Decision

- **Use compiled FIBO as the authoritative SOURCE + provenance, not as a direct
  extraction guardrail.** Guardrails should be curated/alias-bridged slices
  derived from the catalog (its `label_index` + the compatibility report are the
  bridge), version-pinned to the FIBO commit via `catalog_provenance`.
- The selector keeps choosing the corpus-fit slice; the catalog supplies the
  citable upstream commit/hash for the chosen guardrail.
- Follow-up: **alias-bridge** official FIBO classes to the LLM's generic
  vocabulary (e.g., `JointStockCompany`/`PubliclyHeldCompany` → alias `Company`)
  using the catalog `label_index`, then re-measure coverage — the path to making
  official modules usable. The ambiguity-mapping loop (seocho-2mg) is the natural
  mechanism (map generic surfaces → FIBO classes).

## Consequences

- The upstream pipeline (ADR-0132/0133) and the guardrail stack are connected and
  measured end-to-end on real FIBO, with an honest result: raw upstream needs
  alias-bridging before it can serve as an LLM guardrail.
