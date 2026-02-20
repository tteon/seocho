# ADR-0016: Runtime Raw Ingest and Local Verification Path

- Date: 2026-02-20
- Status: Accepted

## Context

The platform had architecture and philosophy guidance, but practical first-mile verification remained weak for operators who want to:

1. inject raw data quickly,
2. see graph changes immediately,
3. validate via chat interface without heavy setup.

Operational issues also surfaced:

- extraction service port collisions in shared docker hosts,
- data loading path not always scoped to target database,
- non-user graph names appearing in routable database lists.

## Decision

Adopt a runtime verification path with explicit product surfaces:

- add `POST /platform/ingest/raw` for runtime raw-text ingestion into target graph database,
- add custom platform UI controls for raw ingestion (`Ingest DB`, `Raw Records`, `Ingest Raw`),
- add deterministic fallback extraction when LLM extraction is unavailable (for local smoke verification),
- make extraction service host port mapping configurable in `docker-compose.yml`,
- scope graph loading to explicit database sessions,
- exclude `agenttraces` from user-facing database routing list.

## Consequences

Positive:

- users can verify ingestion-to-chat loop faster with lower setup friction,
- local/demo environments remain usable when LLM key/path is temporarily unavailable,
- reduced environment drift from fixed-port conflicts,
- clearer DB routing boundaries for runtime agents.

Tradeoffs:

- fallback extraction is lower semantic fidelity than LLM path,
- additional endpoint/UI surface increases maintenance and test scope.

## Implementation Notes

- API: `extraction/agent_server.py` (`/platform/ingest/raw`)
- Ingestion runtime: `extraction/runtime_ingest.py`
- UI proxy/static: `evaluation/server.py`, `evaluation/static/index.html`, `evaluation/static/app.js`, `evaluation/static/styles.css`
- DB/session behavior: `extraction/graph_loader.py`, `extraction/database_manager.py`, `extraction/config.py`
- Docs updates: `README.md`, `docs/QUICKSTART.md`, `docs/TUTORIAL_FIRST_RUN.md`, `docs/ARCHITECTURE.md`, `docs/WORKFLOW.md`
