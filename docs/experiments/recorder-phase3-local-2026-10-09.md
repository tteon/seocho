# Recorder Phase 3 local acceptance — 2026-10-09

Phase 3 adds exact pinned-origin replay, audited inference-only base snapshots,
causal branch interventions, N fresh downstream samples, a shared budget sweep
ledger and conservative pre-dispatch token/dollar admission. Original records
remain immutable. The derived `interventions` SQL view links base and derived runs.

Operators covered: local budget, uniform/difficulty schedule, model/agent route,
tier, reference knock-in, explicit or source-run plan replacement, and subanswer
replacement. Unaffected information results are frozen with origin-event references.
Injected plan/subanswer context is checked against gold before agent delivery;
unsafe replacement text is excluded from inference event metadata as well.

Actual loopback HTTP acceptance ran one base and repeated the same reference
knock-in twice, each with N=2. Results: five runs, 25 call records, 118 agent events.
The four derived runs each had two pinned prefix hits and three fresh downstream
calls. Totals: eight prefix cache hits; actual Mock server dispatch count 17
(base five + downstream twelve). Every derived manifest and event-pair/call-link
audit passed. The intervention view had one distinct base and four derived IDs.

Validation:

- `python examples/agent-observability/acceptance_phase3.py --directory outputs/agent-observability/phase3-reviewed-acceptance`: passed actual loopback acceptance.
- Recorder/harness/intervention focused tests: 17 passed; intervention tests rerun after replacement-gold metadata protection: 3 passed.
- `bash scripts/ci/run_basic_ci.sh`: 718 passed, 5 skipped; Ruff, runtime/module ownership, root hierarchy and agent-doc checks passed.
- `python scripts/ci/check_adr_index.py`, `bash scripts/ci/check-doc-contracts.sh`, `git diff --check`: passed.

Services: deterministic local Mock and Recorder, actual DuckDB 1.5.6, existing
Python 3.10.20 repository venv. No MARA, OpenAI or graph service was called.
This is replay/orchestration protocol evidence, not answer quality, causal effect,
external compatibility, performance or production readiness evidence.

Limits: independent branches and one non-streaming structured Chat call per step;
no dependent DAG engine or arbitrary framework recovery. Plan replacement consumes
recorded/provided decomposition without silently making a paid planner call.
Cost caps use retained conservative reservations and user-supplied versioned price
upper bounds, not inferred provider prices or billing guarantees. Unknown usage
stops further work. No cost/quality effect is inferred from one derived answer;
population statistics belong to Phase 5.

See [commands and operator contracts](../AGENT_OBSERVABILITY.md). Phase 4 requires
end-of-phase confirmation under the supplied implementation directive.
