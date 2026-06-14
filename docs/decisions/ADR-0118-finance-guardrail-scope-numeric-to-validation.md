# ADR-0118: Scale-Validated Guardrail Value Is Structural-Not-Numeric → Scope Numeric Guarantees to SHACL Validation

Date: 2026-06-14
Status: Proposed

## Context

ADR-0115/0116 established, on a 160-doc FinDER sample, that a richer ontology guardrail improves
LLM extraction and that corpus-aware scoring predicts that value. Before committing the product
thesis ("ontology-governed graph memory makes financial LLM/RAG safer and more correct") we needed
(a) a larger-sample replication and (b) an external evidence base for *which* correctness the
ontology actually buys — because the FinDER data already hinted that numeric categories barely
benefited.

Two inputs informed this ADR:

1. **Scale replication (measured).** FinDER guardrail ablation at **N=320** (40 × 8 categories,
   all 4 MARA models, 2,560 thread-pooled extractions) + corpus-aware scorecard over a 268-type
   open-extraction profile. Data: `ADR-0118-scale-validation-summary.json` (full per-doc records
   retained out-of-repo).
2. **Problem-definition survey (deep-research, 23 verified / 2 refuted claims from authoritative
   sources).** Full writeup in the Obsidian vault (`wiki/finance-ontology-problem-definition.md`).

## Findings

### Scale replication confirms and sharpens ADR-0115

| arm | extraction_score | conformance | nodes/doc |
|---|---|---|---|
| sparse (fibo_minus, 2 classes) | 0.733 | 0.745 | 4.29 |
| rich (fibo_plus, 9 classes) | **0.891** | **0.902** | 8.56 |
| Δ | **+0.158** | +0.158 | +4.27 |

Replicates the N=160 result (+0.164) almost exactly; positive for **all four models** (DeepSeek
+0.134, MiniMax-M2.5 +0.241, gpt-oss-120b +0.237, MiniMax-M2.7 +0.020). Corpus-aware scoring again
moved the sparse ontology from rank-1 (intrinsic) to rank-3 (guardrail+corpus), matching downstream.

**The benefit is category-conditional, now decisively:**

| entity-rich (large gain) | Δscore | | numeric-heavy (≈0 / negative) | Δscore |
|---|---|---|---|---|
| Governance | **+0.546** | | Footnotes | +0.019 |
| Risk | **+0.324** | | Financials | **+0.002** |
| Legal | +0.163 | | Shareholder return | **−0.029** |
| Accounting / Company overview | +0.138 / +0.130 | | | |

### Survey establishes the problem and the same tension

- Integration is real and costly: managing financial data without common standards "runs into the
  **billions of dollars**" (OFR); FIBO (OWL 2 DL) + LEI are the canonical ontology + identity
  remedies — though the LEI alone does **not** solve entity resolution (~99% of global entities
  unregistered).
- Numbers are where LLMs fail: FinQA best **61%** vs **91%** human; ConvFinQA **<70%** vs 89%;
  FinanceQA **~60%** task failure; GPT-4-turbo **9%** closed-book. Regulators (FSB, BoE, GAO) name
  model risk / data quality / hallucination as material-to-systemic.
- **The honest tension (independently surfaced by the survey):** ontology/KG grounding fixes entity
  identity, closed-vocabulary extraction, typed links, provenance, and disambiguation — **but it
  does not perform arithmetic.**

### Convergence

The survey's tension and our N=320 measurement agree: the guardrail soars on entity-rich domains
(Governance +0.55) and is flat/negative on numeric-heavy ones (Financials +0.002, Shareholder
return −0.03). Two independent methods — external literature and in-house measurement — converge on
**guardrail value is structural, not arithmetic.**

## Decision

1. **Scope the product's numeric correctness claim to validation, not computation.** SEOCHO must
   NOT claim "the ontology produces correct numbers." It should target **numeric-fact validation**
   via SHACL/constraint checks over extracted facts (unit/scale, fiscal period, reconciliation,
   materiality, cross-entity consistency) — catching wrong numbers, not computing them. This is
   the **P3** workstream and the next experiment (does SHACL catch LLM numeric errors, and what
   fraction are structurally catchable vs inherently arithmetic?).
2. **Keep the structural claim, which is measured and strong:** identity, closed-vocabulary
   extraction, typed relationships, provenance, disambiguation-before-retrieval. Use corpus-aware
   `profile="guardrail"` scoring (ADR-0116) to choose/justify a guardrail per corpus.
3. **Prioritize `seocho-ub5` (provider-aware meta-prompt).** MiniMax-M2.7 failed ~16% of
   extractions on JSON parsing (vs 0% for the other three) — a concrete reliability gap that costs
   ensemble coverage.

## Consequences

- Product framing is now evidence-backed and honest about the numeric boundary.
- P3 (SHACL numeric validation) becomes the headline next experiment — it addresses the exact gap
  the survey flagged ("no source measured a KG-grounding intervention's effect on numeric
  benchmarks") and the exact category (numeric) our guardrail did not help.
- Tickets: `seocho-g2r` (eval), `seocho-ub5` (provider meta-prompt), plus a new P3 numeric-validation
  ticket. Identity long-tail (FIBO+LEI bridging) tracked under `seocho-uxs`.
