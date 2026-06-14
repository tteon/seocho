# ADR-0115: OntoClean Meta-Property Critic — Refined Ontology as an LLM Guardrail

Date: 2026-06-13
Status: Proposed

## Context

ADR-0114 gave SEOCHO a graded ontology **scorecard** (the measurement instrument). Its
`taxonomy_health` tier detects *structural* smells (orphans, flatness, single-child branches)
but cannot tell whether an is-a edge is **formally** correct. "Person is-a Student" is
well-formed structurally yet ontologically wrong: being a Person is permanent, being a Student
is a revocable role.

OntoClean (Guarino & Welty, 2002) is the discipline's answer: tag each class with four formal
meta-properties — **rigidity, identity, unity, dependence** — and check that every subsumption
edge respects the constraints those tags impose. The expensive part of OntoClean is the manual
tagging; an LLM (MARA, per the user's authorization) can propose the tags.

The product purpose (user framing, 2026-06-13): the scorecard + OntoClean critic + versioning
form an **ontology refinement pipeline**, and the *refined, versioned* ontology is then used as
an **LLM guardrail** (the extraction/query enforcement layer, ADR enforcement modes
strict/guided/open). The metric that ultimately matters is the **LLM before/after difference**
when a refined-ontology guardrail is in force versus a draft/no guardrail — i.e. exp-001's
ontology-on/off ablation, but with "on" supplied by the governed pipeline rather than an
ad-hoc schema.

## Decision

Add `seocho.ontology_ontoclean` with two cleanly separated halves:

1. **Pure constraint engine** — `check_ontoclean(ontology, tags) -> OntoCleanResult`. Encodes
   the four OntoClean subsumption constraints, deterministic, no I/O, fully unit-tested. A
   `None` (unknown) tag on either endpoint skips that constraint, so unknowns never produce a
   false violation.
   - **Rigidity (C1):** an anti-rigid class cannot subsume a rigid one (flagship check).
   - **Identity (C2):** identity is inherited downward; a subclass cannot drop a carried
     identity criterion, and incompatible own-identity keys along a chain are warned.
   - **Unity (C3):** a +U class and a ~U class cannot stand in is-a.
   - **Dependence (C4):** a dependent class cannot subsume an independent one.
2. **Injectable inference** — `infer_metaproperties(ontology, *, backend)` uses an LLM backend
   (`create_llm_backend(provider="mara")` by default) to *propose* the tags. The backend is
   injected, never constructed inside the engine, so: (a) the engine and tests run offline; (b)
   the **scorecard stays LLM-free** — it consumes precomputed tags via the new
   `score_ontology(..., ontoclean_tags=...)` parameter and folds hard violations into
   `taxonomy_health`. This honours the "explicit opt-in over magic" principle: nothing fires a
   model implicitly.

Tags are serialisable (`dump_/load_metaproperties`) so an inferred tag set is cached and
recorded as part of the data trail (every MARA use must be recorded — user requirement).

## Validation

### 1. OntoClean critic — deterministic before/after (measured, offline)

`docs/decisions/ADR-0115-ontoclean-experiment.json`. A 4-class role ontology where the draft
mis-models `Person broader Student` (rigid under an anti-rigid, dependent role); the fix makes
`Person` a root with `Student`/`Employee` as roles beneath it. Hand-authored tags (the tags an
LLM would propose).

| | OntoClean | overall | grade | taxonomy_health |
|---|---|---|---|---|
| **before** (Person<:Student) | 2 violations | 0.781 | C | 0.250 |
| **after** (fixed hierarchy) | 0 | 0.919 | A | 0.750 |
| **Δ** | −2 | **+0.139** | C→A | **+0.500** |

The one wrong edge tripped **two** independent constraints (rigidity *and* dependence) — exactly
the redundant, formally-grounded signal OntoClean is prized for. Tests:
`tests/seocho/test_ontology_ontoclean.py` (10) + scorecard integration.

### 2. Live MARA ensemble + guardrail ablation (measured 2026-06-13)

`scripts/benchmarks/ontology_guardrail_ablation.py`, provider MARA
(`api.cloud.mara.com`), all four models (DeepSeek-V3.1, MiniMax-M2.5, MiniMax-M2.7,
gpt-oss-120b). Record: `docs/decisions/ADR-0115-guardrail-ablation.json`.

**Phase A — ensemble OntoClean refinement.** A draft business ontology with a *planted* defect
(`Person broader Employee` — rigid Person under the anti-rigid role Employee). Each model
independently inferred meta-properties; majority vote = consensus. **All three models that
returned parseable JSON agreed: Person rigid=True, Employee rigid=False, Employee dependent=True**
(MiniMax-M2.7 emitted unparseable reasoning in Phase A and was outvoted-by-absence — the ensemble
is robust to one model failing). The critic flagged the edge on **two** constraints (rigidity +
dependence); fixing the hierarchy cleared both.

| | OntoClean | overall | grade |
|---|---|---|---|
| draft guardrail | 2 violations | 0.597 | F |
| refined guardrail | 0 | 0.956 | A |

**Phase B — guardrail payoff.** The same 6 documents were extracted by each model twice: with the
draft ontology as the injected guardrail (arm A) vs the refined ontology (arm B), scored against
the refined target schema. Cross-model means (all 4 models, consistent direction):

| arm | label conformance | extraction_score | distinct labels |
|---|---|---|---|
| A — draft guardrail | 1.000 | 0.968 | 3 |
| B — refined guardrail | 1.000 | **1.000** | 4 |
| **Δ (B−A)** | 0 | **+0.032** | +1 |

Reading (honest): both guardrails hold the LLM inside the vocabulary (conformance saturates at
1.0 on this small/clean corpus), so the *enforcement* effect is equal. The refined guardrail's
gain is **structural completeness** — its identity keys / unique constraints / definitions make
the model fill the properties that score the extraction perfectly (1.0 vs 0.968), and it surfaces
the extra entity type (Employee) the draft had malformed. The effect is small but **consistent
across all four models** and isolates exactly what the governance pipeline buys downstream: not
"more in-vocabulary" but "more completely and identifiably structured". A larger/noisier corpus
is expected to widen the conformance gap too (exp-001 follow-up).

The deterministic hand-tagged variant (`ADR-0115-ontoclean-experiment.json`) corroborates the
Phase A constraint logic without a model in the loop.

### 3. Large-corpus guardrail ablation on FinDER (measured 2026-06-13)

`scripts/benchmarks/finder_guardrail_ablation.py`, corpus `Linq-AI-Research/FinDER` (5,703
SEC-filing cases), **160-doc category-stratified sample (20 × 8 categories)**, all four MARA
models, references truncated to 3,000 chars, 16-thread pool (1,280 extractions). Arms are two
shipped FIBO variants used as extraction guardrails: **A = fibo_minus (2 classes)** vs
**B = fibo_plus (9 classes)**. Each extraction scored against its own guardrail. Record:
`docs/decisions/ADR-0115-finder-guardrail-ablation.json`.

Cross-model means:

| arm | nodes/doc | label conformance | extraction_score | distinct labels |
|---|---|---|---|---|
| A — sparse (fibo_minus) | 4.01 | 0.746 | 0.734 | 2.25 |
| B — rich (fibo_plus) | 8.25 | 0.909 | 0.898 | 9.5 |
| **Δ (B−A)** | **+4.24** | **+0.164** | **+0.164** | +7.25 |

**The result inverts the toy-corpus reading and is far stronger.** On real, heterogeneous
financial text a *too-sparse* guardrail HURTS: with only `Company`/`FinancialMetric` to choose
from, the model is forced to mislabel the people, regulations and risks the text contains, so
~25 % of its labels fall out of vocabulary (conformance 0.746). The richer guardrail gives the
model adequate labels → higher conformance, higher score, ~2× coverage. Holds for **all four
models** (Δscore DeepSeek +0.145, MiniMax-M2.5 +0.234, gpt-oss-120b +0.238, MiniMax-M2.7 +0.041).

Honest caveats:
- **MiniMax-M2.7 emitted unparseable JSON on ~20 % of docs** (31–32 / 160 per arm); its numbers
  are over the successful subset and are the noisiest. The other three models had 0 parse errors.
- **The benefit is strongly category-conditional** (cross-model Δscore by category):

  | category | Δ extraction_score | conformance A→B |
  |---|---|---|
  | Governance | **+0.476** | 0.50 → 0.97 |
  | Risk | **+0.423** | 0.55 → 0.97 |
  | Company overview | +0.197 | 0.75 → 0.95 |
  | Legal | +0.138 | 0.77 → 0.91 |
  | Accounting | +0.073 | 0.87 → 0.93 |
  | Footnotes | +0.047 | 0.86 → 0.91 |
  | Financials | +0.005 | 0.72 → 0.73 |
  | Shareholder return | **−0.021** | 0.97 → 0.95 |

  Entity-rich domains (Governance, Risk) gain enormously; metric-dominated domains (Financials,
  Shareholder return) gain nothing or regress slightly because the sparse schema is already
  adequate and the extra labels add noise. This mirrors exp-001's "ontology helps in enumerable
  domains" and refines the product claim: **the value of a richer ontology guardrail is
  conditional on whether the domain's entities exceed the minimal vocabulary.**

**Implication for the scorecard (feedback into ADR-0114).** The scorecard rated fibo_minus
(0.90, B) *above* fibo_plus (0.82, B) — penalising fibo_plus's flatness — yet fibo_plus is the
better guardrail overall. Intrinsic structural grade and downstream guardrail value diverge: the
"best" ontology is corpus/task-dependent. This argues for (a) corpus-aware weighting (a guardrail
use case should weight `constraint_richness`/coverage over taxonomy depth) and (b) running the
functional tier against real target documents, not CQs alone. Tracked as a follow-up on
`seocho-g2r`.

## Consequences

- The scorecard now grades is-a edges for *formal* correctness, not just structure — the
  rigorous core of "axiom adjustment / taxonomy design".
- OntoClean tagging, historically the methodology's bottleneck, becomes a single recorded MARA
  call; tags are cached and versioned with the ontology.
- Ties the intrinsic quality pipeline (scorecard + OntoClean + versioning) to the extrinsic
  payoff (LLM-guardrail before/after), closing the measure→refine→prove loop.
- Follow-ups (off `seocho-g2r`): the live MARA-tagged guardrail ablation; persisting tags into
  the version snapshot store; a `seocho ontology ontoclean` CLI subcommand.
