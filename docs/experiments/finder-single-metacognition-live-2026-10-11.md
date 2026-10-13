# FinDER single-agent metacognition: live gate, 2026-10-11

Opt-in SDK single-agent metacognition was exercised through MARA and Recorder,
cache off, in the isolated Tailscale execution tree. No graph database, ontology
service or specialist was used. MARA inference model: gemma-4-31B-it; evaluator:
DeepSeek-V3.2. Provider revision, hardware and prices are unavailable. This is an
interface/observability pilot, not a model ranking or performance claim.

Workload: original FinDER 7e6c28fd Financials/reasoning=True/Subtract with six
question-selected fixed filing passages, and the prepared missing_evidence
variant with zero passages and changed insufficiency gold. Source/generator
identities and the private golden artifact are pinned by the prepared manifest.
One seed (42), one sample each, sequential trials. Human review is pending.
Initial and final answers use the same model, 2048 output cap; evaluator 2048.
Limits: 24 physical requests, 49,152 reserved output tokens, 12 calls/run,
6 tools/run. Actual: 9 requests, 18,432 reserved output tokens. Reservation is
not monetary cost and excludes input tokens. No retries or cache replay.

Both initial assessments and final inference answers completed. Original:
assessment said answerable with confidence 1 and cited a $633 million change;
final answer retained that result, with zero tools. Missing evidence:
assessment reported missing operating-earnings slots and requested search;
two successful searches returned empty evidence, then final answer abstained.
This duplicate empty search is visible extra work; it does not establish a
semantic tool-necessity verdict. Both runs failed at final evaluation with
HTTP 429. Initial judging was therefore not reached. Final/initial correctness,
recovery, regression, confidence calibration and failure-detection quality are
unknown. Do not count these judge failures as answer-model failures or mark the
$633 answer correct without checking periods and source support.

Call manifests: 5 and 4 calls, complete coverage, both valid. Agent-event audits:
22 and 16 events, both valid. Source assessments are validated against recorded
originals by local quality analysis. Recorder was stopped after the gate.
Private artifacts: outputs/agent-observability/single-metacognition-live-20261011.

The gate used the first assessment instruction version, which described
answerable as predicted correctness. After inspection this ambiguity was fixed:
answerable now means substantive evidence sufficiency, confidence predicts
correctness including justified abstention. Failure prediction uses predeclared
confidence < 0.5 separately from answerability. No new live call was made after
that correction; amended semantics have deterministic contract tests only.
The local analyzer was extended after execution to audit initial records; raw
results retain original implementation hashes. Ontology/KG requests remain
unavailable rather than fabricated, and no randomized no-tool counterfactual or
human-calibrated detection labels have yet been evaluated.
