# Recorder Phase 2 local acceptance — 2026-10-09

Phase 2 implements YAML conditions, explicit orchestration decisions/realizations,
versioned SDK prompts, independent run/seed/sample identity, shared call admission,
soft/global/local budgets, hard API output caps, static/LLM model routing and
forced/agent-decided information policies. It retains fixed FinDER passage inputs.

User-selected information policy: prepare agent reference separately from gold;
use existing fixed passages; prioritize static routing with LLM routing as another
condition; no contamination variants supplied. The actual MSI FinDER case
`7e6c28fd` was prepared locally into separate agent cases and evaluation JSON.
Question-only source selection remains the original six passages. Exact gold
substring filtering excluded no passage in this pilot; this is not a semantic
leakage guarantee or human curation. No FinDER model/quality rerun was performed.

Actual local HTTP acceptance: 20 conditions, 88 LLM calls, 388 agent events,
cache off. Eighteen forced conditions cover six named strategy variants × three
tiers; two additional conditions cover agent-decided information with static/LLM
routing. All caller manifests matched transport records, all event pairs and
parent/call links passed audit. Observed usage, model identity and budget compliance
are stored with realizations. Mock evaluations explicitly say contract-only.

Validation:

- `python examples/agent-observability/acceptance_phase2.py --directory outputs/agent-observability/phase2-reviewed-acceptance`: actual local HTTP matrix acceptance.
- `pytest tests/seocho/test_agent_observability_harness.py tests/seocho/test_recorder.py -q`: 14 passed, including leak blocking, isolated evaluator requests, routing to different model mappings, independent sample identity, cache rejection and soft/hard budget separation.
- `bash scripts/ci/run_basic_ci.sh`: 715 passed, 5 skipped; Ruff and module/runtime/root/doc checks passed.
- `python scripts/ci/check_adr_index.py`, `bash scripts/ci/check-doc-contracts.sh`, and `git diff --check`: passed.

Actual services: loopback deterministic mock + Recorder, real DuckDB 1.5.6, Python 3.10.20 (the existing repository venv).
No MARA, OpenAI or graph backend was called; results are protocol/orchestration
acceptance, not external compatibility, quality, latency or throughput evidence.

Known limits: independent subtasks only; no dependent DAG scheduler, cross-run
semantic alignment, prefix intervention engine or graph UI. Conservative exact
checks do not detect paraphrase leakage. Gold separation is a trusted local data
contract rather than authenticated role access. Provider pricing/revision and
unreported reasoning remain unknown. Failure manifests are preserved; failed
experiments exit unsuccessfully even when later conditions complete. Beads status
was checked, but the local database is absent; no tracker was reset or initialized.

See [execution and inspection commands](../AGENT_OBSERVABILITY.md). Phase 3
requires the supplied directive's end-of-phase confirmation.
