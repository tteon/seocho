# ADR-0135: Alias-Bridging Official FIBO to LLM Vocabulary — Measured Coverage Lift

Date: 2026-06-14
Status: Proposed

## Context

ADR-0134 measured that official FIBO modules score near-zero corpus_coverage on
FinDER because their fine-grained labels (`JointStockCompany`) don't match the
LLM's generic extraction vocabulary (`Company`). The identified fix: **alias-bridge**
official classes to the generic terms, then re-measure.

## Decision

Add `seocho.fibo_catalog.alias_bridge(ontology, terms)` and `bridge_to_corpus(
ontology, corpus_profile)` (offline/deterministic): add each generic ``term`` as
an alias to every class whose **token-set contains the term's token-set**
(camelCase-aware), e.g. `Company` → alias of `JointStockCompany`/
`PubliclyHeldCompany`. Token-**subset** matching (not raw substring) avoids
spurious hits — `Date` ⊄ `Candidate`, `FinancialMetric` ⊄ `FinancialInstrument`.

## Validation (measured, real compiled FIBO @ fee10a4, FinDER corpus)

`docs/decisions/ADR-0135-alias-bridge.json`. corpus_coverage before → after
bridging to the FinDER open-extraction labels:

| module | classes | coverage before | after | Δ | aliases added |
|---|---|---|---|---|---|
| BE | 193 | 0.008 | 0.181 | +0.174 | 45 |
| FBC | 515 | 0.093 | **0.316** | +0.222 | 208 |
| FND | 437 | 0.199 | 0.300 | +0.101 | 71 |
| SEC | 795 | 0.005 | 0.192 | +0.186 | 78 |

Alias-bridging **lifts coverage substantially** (SEC 0.005→0.192 = ~38×; FBC
nearly 3.4×) — confirming ADR-0134's granularity-mismatch diagnosis: the official
classes ARE relevant, their *labels* just didn't match the LLM vocabulary.

**Honest limit:** even bridged, the best official module (FBC 0.316) stays below
the curated `fibo_plus` (0.595, ADR-0134). Lexical token-subset bridging closes
roughly half the gap; the rest needs semantic mapping (a FIBO `Officer`/`Director`
→ `Person` link isn't lexical) — the ambiguity-mapping loop (seocho-2mg) or
`same_as`/broader-root bridging.

## Consequences

- A measured, deterministic lever to make official version-pinned FIBO usable as a
  guardrail candidate: bridge → re-score → the selector can now consider official
  modules competitively (not auto-lose at coverage ~0).
- Recommendation stands (ADR-0134): compiled FIBO is the authoritative SOURCE;
  produce a *bridged* (and ultimately curated) slice from it rather than injecting
  raw modules.
- Follow-ups: semantic bridge (broader-root / `same_as` / LLM map of generic→FIBO)
  to close the residual gap to curated; combine bridged modules; re-run the
  answer-accuracy experiment with a bridged module once it's prompt-sized.
