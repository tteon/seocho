# Matched FinDER single-agent / multi-agent coordination

Status on2026-10-12: implementation and validation complete; live execution
blocked before launch. Existing SSH host<private experiment server> times out and is absent
from this computer's current Tailscale network map. No new MARA request was
made for this cohort. No mock result replaces the missing live gate.

The protocol assigns240 trials:12 previously frozen original FinDER questions
×2 models (Gemma4-31B-it and gpt-oss-120b) ×5 designs ×2 samples. The original
reasoning boolean is balanced six/six, spans twelve companies and remains
private evaluator metadata. Cases were not reselected from failed outputs.

| Arm | Context and execution |
|---|---|
| direct | One solver answers from fixed sources |
| single_decomposition | One actor solves fixed tasks sequentially, receives prior findings, synthesizes |
| independent_parallel | Separate task actors solve concurrently, then synthesis |
| sequential_context | Separate actors receive prior task findings, then synthesis |
| peer_review | Independent task actors exchange findings, revise once, then synthesis |

All planned arms use the same two public tasks: required source facts and
verification of relations/calculations/units/periods/assumptions. This is generic
role decomposition, not proof of an optimal question-specific plan. All use the
same frozen source inventory and solver/synthesis model. Single versus separate
sequential identities is an identity-only negative control: no persistent SDK
session memory, native handoff or Agent.as_tool claim. Orchestration is
host-scheduled SDK agents with explicit context packets. Names alone should not
produce a quality claim.

The common answer judge is DeepSeek-V3.2, not either inference model. Every arm
sets judge_coordination=False and uses DetailedEvaluation so the answer rubric
is not extended only for context-exchange arms. Actual returned context source
steps, separate identities and review outputs are recorded. Semantic context
use, correction and synthesis fidelity require separate human review; output
changes or shared citations do not prove improvement or redundant work.

Per-run eight physical calls, inference1024 and judge2048 output ceilings;
whole suite1920 calls and3932160 reserved output tokens. Actual consumption
differs and must be reported. Input tokens and money are not reserved; prices
remain unknown. Serial trials, parallel task calls only in designated arms,
cache off, no retries, fixed seed42 with two independent requests (provider
seed support unknown). Latency sums are not wall-clock latency or throughput.

Private prepared cases, protocol and config:
outputs/agent-observability/multi-agent-matched-20261012. The guarded run_live.py
refuses existing results, starts Recorder8837, stops it in finally, analyzes
results, audits every run and generates comparison.md/json and a human-review
queue. Run only from the isolated server checkout once authorized connectivity
returns. Credentials come from environment or SEOCHO_CREDENTIAL_FILE, never
from source code. All paid calls pass the Recorder.

Validation: focused pytest quality_lab/agent_tools/recorder_ui22 passed;
bash scripts/ci/run_basic_ci.sh764 passed,5 skipped; ruff, ADR index and diff
checks passed. These validate contracts, not MARA model compatibility or quality.
Previous360-trial cohort finished with229 completed/131 failed,235 answers and
1553 requests; all original call/event audits passed. Its model judgments are
not human truth and it is not pooled with this new matched design.
