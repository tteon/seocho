# ADR-0238: FinDER failure, coordination and robustness experiments

Status: Accepted, experimental. Date: 2026-10-11.

## Context

Recorder captures actual model traffic, but aggregate answer scores cannot explain
provider failures, evidence mistakes, agent context loss or evaluator mistakes.
FinDER supplies original domain/reasoning labels and evaluation-only answers.
Controlled transformations and matched interventions are needed to test improvement.

## Decision

Add an opt-in quality-lab CLI under the canonical evaluation package. Generate
source-linked, purpose-labelled deterministic variants, keeping original and
synthetic scores separate. Question-only evidence anchors permit position tests
without choosing context from gold. Empty-evidence and fictional numeric variants
have new explicit gold contracts. Human-review queues remain pending; generated
candidates are not automatically golden truth. Document/company groups share a
split, and overlapping groups that straddle splits fail validation.

Add shared public task plans, sequential context delivery, peer review and bounded
adaptive review to the OpenAI Agents SDK harness. Keep current independent controls
and fixed passage delivery. Gold and domain labels remain evaluator-only; review
must preserve uncertainty and supported disagreement. Reject unsupported Pi/replay
combinations. Every physical model request consumes both a per-run admission cap
and optional suite-wide call/output reservation cap, including evaluator requests.

Store tool/context decisions in Recorder and derive versioned failure labels from
those immutable facts. Check report identities, judgments, receipts and manifests
before analysis. Separate inference failures from evaluator/provider failures.
Paired budget outcomes are over/under-thinking candidates, not diagnoses of private
reasoning. Champion/challenger runs produce observations; promotion stays disabled.

## Consequences and validation

No production graph backend or ontology policy changes. Fixed-context experiments
cannot establish retrieval, ontology or graph quality. Unknown scores, service
failures, truncated outputs, unexecuted trials and evaluator cost stay visible.
Human calibration, independent judges and repeated heldout evidence are required
before claiming improvement. Focused tests inspect actual Recorder requests and
causal event coverage; real MARA gates establish only their documented compatibility.
See `docs/AGENT_OBSERVABILITY.md`, `docs/execplans/finder-quality-lab.md` and the
quality-lab live experiment report.

Single-agent extension (2026-10-11): opt-in public epistemic assessments precede
bounded tools without specialists. Initial and revised answers are judged
separately, with evaluation-only gold and shared physical call limits. Missing
ontology/KG capabilities are reported, never fabricated. Paired model labels
require human calibration and a no-tool counterfactual before causal claims.

Public execution-path extension: compare direct, explicit ordered decomposition
and adaptive decomposition/tools with fixed solver model/evidence/caps. Preserve
actual child-call identities and missing slots. Gold-free path judging is a
separate actor from gold-based answer judging; provider failures retain partial
inference. Public plans do not expose private reasoning. No production routing
or graph construction change is introduced.

2026-10-12 extension: matched coordination adds an opt-in single worker identity
for fixed sequential-context tasks and synthesis. It is an identity-only negative
control against separate sequential agents with the same explicit payloads,
not persistent agent memory. Parallel independent and peer-review conditions
continue to be host-scheduled SDK instances. Same question sources and bounded
admission are required; no native handoff or identity-driven quality claims.
