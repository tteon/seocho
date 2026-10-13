# Recorder Phase 4 derived-signal acceptance — 2026-10-09

Phase 4 implements subtask alignment, matched tier transitions/retrieval harm,
silver consensus and first divergence, paired reference knock-in rescue, answer
cluster entropy/tier changes and observed information/subtask mapping. Analysis
reads audited Recorder records and writes a separate versioned DuckDB database.
No inference agent receives silver labels or evaluation artifacts automatically.

Actual stored-record acceptance used the earlier real loopback Phase 2 and Phase 3
Recorder databases: 20 + 5 runs. Results contained 44 alignment rows and 28 observed
information-delivery mappings. Identical recomputation returned identical job IDs.
These sources have Mock evaluator verdicts, so correctness-dependent signals were
excluded: no silver labels, correctness transitions or rescue scores were invented.
Unknown/missing/ambiguous evidence is retained in explicit gap rows.

Scripted Recorder-backed tests separately verified positive paths with six tier
runs plus N=2 knock-in runs: two silver labels, six matched tier transitions,
first silver divergence, one-bit entropy reduction and paired rescue. Scripted
verdicts are deterministic mathematical fixtures, not FinDER quality evidence.
Toy precomputed vectors verified semantic clustering computation and immutable
model/revision/vector-file provenance; no pretrained semantic model was run.

Validation:

- `python -m seocho.recorder signals --workspace phase2-mock-lab --directory outputs/agent-observability/phase2-reviewed-acceptance/store --output outputs/agent-observability/phase4/phase2-signals.duckdb`: passed stored-source analysis.
- Same command against `phase3-reviewed-acceptance/store`: passed. Results and recomputation checks are saved locally in `outputs/agent-observability/phase4/acceptance.json`.
- `pytest tests/seocho/test_agent_signals.py -q`: 8 passed.
- Focused Recorder/harness/intervention/signal tests: 25 passed.
- `bash scripts/ci/run_basic_ci.sh`: 726 passed, 5 skipped; Ruff, runtime/module ownership, root hierarchy and agent-doc checks passed.
- `python scripts/ci/check_adr_index.py`, `bash scripts/ci/check-doc-contracts.sh`, `git diff --check`: passed.

Environment: actual DuckDB 1.5.6, Python 3.10.20 repository venv. No MARA, OpenAI,
graph backend, remote judge or embedding download was used in this phase. The
stored inputs came from earlier local HTTP services; current analysis is offline.
No answer-quality, semantic-model quality, causal-effect or performance claim.

Limits: the default embedding is lexical and its disagreement entropy is named
accordingly. Semantic computation requires supplied local vectors or a versioned
local encoder; missing vectors fail without fallback. Alignment confidence is
heuristic, not probabilistic. Plan-order first divergence is an approximation,
not the earliest true reasoning error. Delivery proves availability, not relevance
or use. Silver labels are not subtask gold. Cached origins/interventions cannot
inflate consensus. Statistical confidence intervals remain Phase 5 work.

See [commands and signal contracts](../AGENT_OBSERVABILITY.md). Phase 5 requires
the supplied directive's end-of-phase confirmation.
