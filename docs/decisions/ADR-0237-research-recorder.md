# ADR-0237: Research Recorder as transport evidence

Status: Accepted, experimental (2026-10-09)

Agent design comparisons require the actual model input/output rather than only
framework events. Add a framework-neutral local proxy in `src/seocho/recorder`,
with a thin runtime shell and an isolated OpenAI Agents SDK adapter. Persist
append-only DuckDB events and SHA-256 blobs; derive audits/cache/export from them.
Keep workspace identity, explicit attempt manifests and separate evaluator domains.

Default independent experiments to cache off; label cache origin and inherited usage.
Unknown provider price/reasoning stays unknown. Fail closed before dispatch when
storage fails. Require Recorder transport in research runners without changing the
normal SDK route, DozerDB backend or operational Opik/OTel observability.

Tradeoffs: one local writer, inspection after shutdown, no streaming cache reuse,
no global network enforcement and no complete decision/tool lineage in Phase 1.
Later phases remain gated by the user's implementation directive. See
[the implementation contract](../AGENT_OBSERVABILITY.md).

Phase 2 extends the same immutable event store with explicit orchestration pairs,
YAML conditions, fixed information plugins and isolated evaluation artifacts. SDK
execution remains in adapters. Independent samples require cache off; static/LLM
routing and forced/agent-decided information policies are separate conditions.
User-authorized FinDER preparation freezes existing source passages and applies
conservative gold checks before information delivery.

Phase 3 uses exact pinned replay from immutable base calls, even when the base was
recorded with cache off. Independent causal branches define the replay frontier;
modified downstream calls remain fresh. Store explicit base/derived links, label
inherited usage and use shared conservative admission caps for N and sweeps.

Phase 4 reads audited immutable evidence into a separate versioned derived store.
Exclude unknown/mock final verdicts from correctness-based signals and replayed
origins from independent consensus counts. Record alignment/embedding provenance;
default lexical proxies do not masquerade as semantic model results or subtask gold.

Phase 5 adds offline pandas/statistical analysis and `obs report` artifacts. Reports
identify audited completed-run scope, missing quality/price evidence, paired sample
coverage, logical/physical replay work and evaluator cost separation. BAM error
predictions are separate from measured E3; best-of-N charges all candidate work.

Phase 6 introduces a packaged offline D3 graph explorer. Raw records remain
read-only; explicitly submitted Mock interventions use isolated copied stores and
Phase 3 pinned replay. Upstream URL and namespace are preserved as request identity,
including an explicit legacy Recorder configuration option. No UI path executes paid
providers. Derived visual badges retain unknown/proxy scope and evaluation isolation.

## 2026-10-11 refinement: optional Pi inference backend

Retain the OpenAI Agents SDK default and common evaluator while allowing an
explicit server Pi CLI inference condition. Pi runs in an isolated configuration
with Recorder headers, a one-request guard, output cap and response acknowledgement.
Prompt/schema placement and streaming differences are part of the measured harness
condition and must be disclosed. In-process Mock transport, UI Mock interventions
and streaming replay remain unsupported for Pi. Actual provider evidence is in
`docs/experiments/finder-pi-harness-live-2026-10-11.md`; local fixtures validate only
contracts. Pi catalog prices do not replace provider/Recorder cost accounting.

## 2026-10-11 refinement: evaluator v2 isolation

Add a versioned opt-in detailed evaluator and separate verification receipt events,
keeping raw model verdicts immutable. FinDER's original reasoning boolean and
operation type are independent stratification labels. Gold references/metadata
stay evaluation-only. Model support, exact quote presence, arithmetic validity and
source semantics remain distinct measurements. Architecture selection requires
calibration and within-stratum comparisons; absent domain/T/F cells stay absent.

2026-10-11 extension: opt-in `tool_agent` exposes model-selected fixed-source
search, numeric receipts/calculation and specialist delegation through the SDK.
Recorder records each turn and tool decision/result; shared admission caps every
physical request. Tool execution checks remain separate from versioned semantic
judgment. Unknown goal-state/minimal-path scores stay unscored, and contradictory
handoff labels are preserved in raw data but excluded from derived pass rates.
This research condition rejects Pi and intervention replay pending separate
integration work. See the tool-agent live experiment report for actual evidence.
