# ADR-0136: Semantic (subClassOf-Root) Bridge Closes the FIBO↔LLM Gap

Date: 2026-06-14
Status: Proposed

## Context

ADR-0135's lexical token-bridge lifted official FIBO coverage on FinDER but
stalled below curated `fibo_plus` (best: FBC 0.316 vs 0.595), because FIBO's
company concept is `LegalEntity` (not lexically "Company") and links like
`Officer`→Person aren't lexical. ADR-0135 named the fix: a **semantic** bridge.

## Decision

Add `seocho.fibo_catalog.semantic_bridge(ontology, root_aliases)` + a domain seed
`FINDER_FIBO_ROOTS`: seed each generic term → FIBO root class(es) and **propagate
the alias DOWN the subClassOf hierarchy** to the root + all descendants (a
subclass of a Company IS a Company). Non-lexical — bridges roots whose own label
doesn't contain the term. Absent roots are no-ops. Offline/deterministic.

`FINDER_FIBO_ROOTS` (5 entries, roots verified in the compiled modules):
`Company`→{LegalEntity, BusinessEntity, FormalOrganization, FinancialServiceProvider,
FinancialInstitution}, `Person`→{Person, ResponsibleParty, AutonomousAgent},
`FinancialMetric`→{Security, Share, DebtInstrument, MonetaryAmount,
FinancialInstrument}, `Regulation`→{LegalConstruct, Agreement, ContractualElement},
`Exchange`→{Exchange}.

## Validation (measured, real FIBO @fee10a4, FinDER corpus)

`docs/decisions/ADR-0136-semantic-bridge.json`. corpus_coverage:

| module | base | + lexical (0135) | + lexical+semantic | vs curated_plus 0.595 |
|---|---|---|---|---|
| FBC | 0.093 | 0.316 | **0.609** | **above** |
| FND | 0.199 | 0.300 | **0.594** | parity |
| SEC | 0.005 | 0.192 | 0.485 | approaching |
| BE | 0.008 | 0.181 | 0.181 | (no financial roots) |

**The semantic bridge closes the residual gap:** FBC reaches **0.609 — exceeding
the hand-curated `fibo_plus` (0.595)** — and FND hits parity. Official,
version-pinned FIBO, lexically + semantically bridged, now matches/beats the
curated slice on corpus coverage. The subClassOf-root propagation was the missing
half (e.g. SEC's `Security`/`Share` roots propagate `FinancialMetric` to 1,249
aliases across hundreds of subclasses).

## Honest caveats

- The seed (`FINDER_FIBO_ROOTS`, 5 mappings) is small **domain curation** — the
  irreducible knowledge "FIBO's `LegalEntity` is the LLM's `Company`". It is
  declarative/reusable and the *propagation* does the heavy lifting, but it is not
  zero-effort.
- **BE doesn't improve** (it has no financial-metric roots) — module/seed fit
  matters; the selector should still pick the best-covering module.
- corpus_coverage is a proxy for label presence; the propagation's *other* payoff
  — runtime extraction matching a doc's `Bank`/`Corporation` to the generic
  `Company` — is real but not measured here.

## Consequences

- End-to-end result for the FIBO line (ADR-0132→0136): official FIBO is the
  authoritative version-pinned SOURCE; a lexical+semantic bridge turns its modules
  into guardrail candidates that **match or beat hand-curated slices**, with the
  FIBO commit/hash as provenance. The hand-made `fibo_*.jsonld` can be retired in
  favor of bridged official modules + a small domain seed.
- Follow-ups: derive the seed from FIBO `same_as`/skos mappings or an LLM
  generic→root map (shrink the manual seed); re-run the FinDER answer-accuracy
  experiment with a bridged FBC module (now prompt-relevant) vs curated.
