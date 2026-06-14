# ADR-0125: `seocho ontology eval-answers` CLI Front Door

Date: 2026-06-14
Status: Proposed

## Context

ADR-0124 added the answer-accuracy eval surface to `seocho.evaluation`
(`AnswerCase`, `evaluate_answer_accuracy`, `compare_guardrails_by_answer`). It is
Python-only: to measure whether an ontology guardrail produces correct answers
over a gold QA set, a user has to write a script that loads the ontology, builds a
backend, hand-rolls `AnswerCase` objects, and calls the function. The other
offline ontology helpers (`check`, `report`, `datahub`, `select-guardrail`)
already have a CLI front door under `seocho ontology`; answer-accuracy did not.

## Decision

Expose the existing surface as `seocho ontology eval-answers` — no new
answer/eval logic, just a thin front door:

- `load_answer_cases(path) -> List[AnswerCase]` in `seocho.evaluation`: pure,
  offline JSON loader. Reads a list of `{question, gold_answer, context,
  category, case_id}` objects; required `question`/`gold_answer`, optional fields
  default to empty; rejects non-list / non-object payloads.
- `ontology eval-answers` subparser in `seocho.cli`: `--schema` (ontology file),
  `--cases` (gold JSON), `--provider` (default `mara`), `--model`, `--workers`
  (default 6), `--json`. Handler loads the ontology and cases, builds a backend
  via `create_llm_backend(provider=, model=)` (credentials come from env only —
  no key files read), runs `evaluate_answer_accuracy`, and prints the overall +
  per-category report (or `report.to_dict()` as JSON with `--json`).

The CLI does not touch hot request paths and adds no new evaluation behavior.

## Validation

- `tests/seocho/test_cli_eval_answers.py` — `load_answer_cases` field mapping,
  optional defaulting, non-list rejection, and loaded cases feeding
  `evaluate_answer_accuracy` over a fake backend (offline; no live backend).
- `bash scripts/ci/run_basic_ci.sh` — full gate (tests + contract checks).
