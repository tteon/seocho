# ADR-0021: Non-Hydra Runtime Config and Ingestion Loader

- Date: 2026-02-21
- Status: Accepted

## Context

The repository stack baseline standardizes runtime on OpenAI Agents SDK + Opik + DozerDB.
Batch/runtime config paths still depended on Hydra/OmegaConf entrypoints, which introduced:

1. unnecessary runtime dependency surface
2. inconsistent config-loading behavior between API/runtime and batch scripts
3. operational confusion about whether Hydra is part of the production runtime contract

## Decision

1. Remove Hydra/OmegaConf dependency from active runtime/batch execution paths.
2. Introduce env-first YAML config loaders in `extraction/config.py`:
   - `load_pipeline_runtime_config(...)`
   - typed dataclass config objects for prompts/model/runtime flags
3. Replace Hydra-decorated batch entrypoints with standard Python CLI loading:
   - `extraction/main.py`
   - `extraction/ingest_finder.py`
4. Keep prompt/schema YAML artifacts under `extraction/conf/*` as plain configuration files.
5. Update docs to reflect non-Hydra config architecture.

## Consequences

Positive:

- reduced runtime complexity and dependency risk
- unified config behavior across API and batch flows
- clearer separation of concerns: Opik for observability, YAML/env for config

Tradeoffs:

- loss of Hydra-specific composition conveniences
- small amount of custom config merge/namespace logic maintained in-repo

## Implementation Notes

Key files:

- `extraction/config.py`
- `extraction/main.py`
- `extraction/ingest_finder.py`
- `extraction/runtime_ingest.py`
- `extraction/conf/config.yaml`
- `extraction/conf/ingestion/config.yaml`
- `pyproject.toml`
- `extraction/requirements.txt`
