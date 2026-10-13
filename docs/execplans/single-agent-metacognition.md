# Single-agent metacognition experiment

## Purpose and Progress
Observe an initial fixed-evidence answer, public missing-slot assessment, bounded
model-selected tools and revised answer before introducing specialist agents.
- [x] Implement opt-in SDK condition and Recorder-backed assessment.
- [x] Validate contracts and basic CI: 759 passed, 5 skipped, 16 warnings.
- [x] Run separately bounded live gate: 2 trials, 9 calls; both judge HTTP429, quality unknown.

## Context and Evidence Contract
Canonical implementation lives in src/seocho/eval/agent_observability. Existing
fixed passages remain the only factual source. Gold/domain/reasoning labels are
private evaluator inputs. Required slots, missing slots and assumptions are
public claims, not private chain of thought or verified failure labels.

## Decision Log and SEOCHO Review Panel
professor_agent: distinguish self-reported uncertainty from verified error;
paired initial/final judgments are required for recovery, and tool execution is
not evidence of epistemic need. software_engineer_agent: reuse tool receipts,
SDK transport and physical call admission; disable specialist tools in this arm.
computer_systems_agent: every assessment and judge consumes the same call gate;
never infer absent reasoning usage or pricing. Falsification: gold in inference,
unrecorded calls, or improved final scores without a paired initial judgment.

## Plan of Work and Interfaces
Add typed EpistemicAssessment and paired evaluator metrics. Add opt-in
Condition.metacognition for tool_agent, pass assessment to a single solver loop,
and retain initial/final evaluations and unknown semantic tool relevance.
Ontology/KG capability requests are observable unmet requests; no fabricated
ontology or graph is supplied. Compare identical fixed passages and model caps.

## Cost, Latency, and Provider Policy
Use existing MARA Recorder transport, OpenAI Agents SDK and fixed-source tools.
No database or OWL runtime. Local tests are contract evidence only. Live gate
must declare a fresh bounded request budget; previous experiment budget is spent.

## Concrete Steps and Acceptance
Run focused tool tests, Ruff, then bash scripts/ci/run_basic_ci.sh. Check initial
assessment and both judgments in evaluation events; specialist absent from tool
schema; unknown metrics preserved. Run failure paths without service calls.

## Idempotence and Recovery
Opt-in default false preserves existing runs. Keep original Recorder stores
immutable and put new live artifacts in separate ignored outputs. No automatic
promotion, gold modification, retries or commits of private data.

## Outcomes and Artifacts
Focused tests: 14 passed. Live manifest/event audits: both valid. Initial assessment and final inference completed, judging blocked by provider HTTP429. Final basic CI: 759 passed, 5 skipped, 16 warnings. See docs/experiments/finder-single-metacognition-live-2026-10-11.md. Beads unavailable in this checkout;
do not initialize/reset private tracker state.

Revision: separated substantive answerability from correctness confidence after the live gate; no live rerun or changed original records. Fixed confidence threshold 0.5; unknown/partial labels excluded from paired correctness metrics.
