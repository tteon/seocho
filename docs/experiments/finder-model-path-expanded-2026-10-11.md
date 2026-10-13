# Expanded FinDER public execution paths

Status: live execution in progress on 2026-10-11. This document is a protocol,
not a completed outcome report. Dynamic progress is in private results.json.

The new cohort model-path-expanded-native-20261011 assigns360 trials:
twelve original questions × five models × direct/explicit/adaptive × two
samples. Original reasoning=False and True each have six questions. Twelve
companies span Accounting, Company overview, Financials, Footnotes, Governance
and Legal. Reasoning=True includes one original type=None record; the boolean
is not inferred from operation type. Domain round robin is not representative
population weighting. Question-only company resolution and passage ranking are
unchanged; each case freezes six original filing passages across all arms.

The five inference models are DeepSeek-V3.1, DeepSeek-V3.2, MiniMax-M3,
gemma-4-31B-it and gpt-oss-120b. Four use API schema; MiniMax uses prompt JSON
with local validation. All arms within a model share its output contract.
Cross-model interface differences remain an explicit confound. Uniform prompt
JSON was initially tested in model-path-expanded-20261011 and stopped after
extensive validation failures; its raw artifacts and29 finalized trials are
preserved. An interrupted in-flight trial may have incomplete lineage and must
not be advertised as a valid completed audit.

Public plans, declared dependencies, returned subquestion contexts and actual
tool attempts are compared. Two independent requests share seed42; provider
seed/determinism support is unknown. Repetition agreement reports both exact
step-question equality and dependency shape equality, neither of which proves
semantic path agreement. Missing plans are reported separately. Private model
reasoning is not requested or reconstructed.

Gold-free path judgment and gold-based answer judgment use Gemma. Gold answer,
references, domains and original reasoning annotations stay evaluator-only.
Gemma self-judging is biased and all judgments require human calibration.
Inference answer availability is reported separately from full evaluation
completion. No design promotion or model ranking follows automatically.

Execution is serial, Recorder-only, cache off and no retries. Global ceilings:
3600 physical requests,7372800 reserved output tokens; ten requests per run
including judges, six successful tool executions. Input tokens and monetary
cost are not covered by the output reservation; provider prices are unknown.
The preliminary contract gate is additional and accounted separately.

The worker stops its dedicated Recorder8836 on normal exit. A one-time
finalizer waits for the worker, audits call coverage and agent events, then
writes comparison.md, comparison.json, audits.json and finalized.json. A local
one-time sync worker mirrors checkpoints and copies the final store/report.
These processes are active work, not recurring automation. Partial results
remain explicit if execution is interrupted or the budget gate stops it.

Private artifacts: outputs/agent-observability/model-path-expanded-native-20261011.
Contract check: bash scripts/ci/check-doc-contracts.sh passed; git diff --check
passed. No engine behavior was edited; prior basic CI763 passed applies to the
unchanged engine, not live model quality. See the expanded ExecPlan for review
lenses and recovery.
