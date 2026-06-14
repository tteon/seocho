# ADR-0124: Answer-Accuracy on the Evaluation Surface

Date: 2026-06-14
Status: Proposed

## Context

ADR-0122 showed that **extraction conformance is not a safe proxy for answer
quality** — they can move in opposite directions (Shareholder return: conformance
0.96→1.0 yet answer accuracy 0.58→0.42). The scorecard (offline, structural) and
the corpus-aware/selector layers reason about conformance/coverage; the evaluation
surface needs a first-class, reusable way to measure the thing users actually want
— **correct answers** — with an ontology in force as the extraction guardrail.

## Decision

Add answer-accuracy primitives to `seocho.evaluation` (the eval surface):

- `AnswerCase` — a gold QA case (question, gold_answer, context, category, id).
- `evaluate_answer_accuracy(backend, ontology, cases, *, judge_backend=, model=,
  workers=)` — per case: extract facts + answer with the ontology as guardrail
  (via the provider-aware structured layer, ADR-0120), then LLM-judge the answer
  vs gold; returns overall + per-category accuracy, label_conformance, and raw
  per-case results. Bounded concurrency with 429-retry (ADR-0122 lesson).
- `compare_guardrails_by_answer(backend, ontologies, cases)` — answer accuracy per
  candidate guardrail; the reusable form of the FinDER answer matrix. **Pairs with
  the offline `guardrail_selector` (ADR-0123): the selector picks offline, this
  validates the pick against gold answers.**

Backends are injected (SEOCHO `LLMBackend` contract) so the surface is testable
with fakes; offline core, online only when a live backend is passed.

## Validation

`tests/seocho/test_answer_eval.py` (5): overall + per-category accuracy, label
conformance, error exclusion, multi-guardrail compare, dict shape — all with a
fake backend (no network).

Live (MARA DeepSeek-V3.1, 15 FinDER cases × 3 categories, 0 errors), record
`ADR-0124-answer-eval-live.json`: sparse guardrail **0.467** → rich **0.667**;
by category Governance 0.4→0.8, Company overview 0.6→0.8 (entity-rich gain),
Financials 0.4→0.4 (numeric, flat) — reproducing ADR-0122 through the new API.

## Consequences

- The eval surface now reports answer accuracy, not just conformance — the safe
  metric for gating guardrail/version decisions and for the
  measure→select→validate loop (scorecard → selector → answer-accuracy).
- Follow-ups: feed answer-accuracy back to learn the selector's numeric-intensity
  threshold (ADR-0123); a second judge / wider N before external claims; a
  `seocho ontology eval-answers` CLI over a gold-cases file.
