# ADR-0080: Internal Orchestration Seams For The Modular Monolith

## Status

Accepted

## Context

SEOCHO's public SDK shape is intentionally small, but internal orchestration is
still concentrated in a few large files such as `seocho/client.py` and
`runtime/server_runtime.py`.

The current stage of the project does not justify a broad microservice split.
The immediate need is clearer internal class seams inside the modular
monolith so we can:

- move behavior out of large facades incrementally
- keep runtime and local SDK paths converging on the same canonical modules
- add traceability and degraded-state handling without mixing concerns

## Decision

SEOCHO will introduce five explicit internal class seams:

1. `DomainEvent` in `seocho/events.py`
2. `IngestionFacade` in `seocho/index/ingestion_facade.py`
3. `QueryProxy` in `seocho/query/query_proxy.py`
4. `AgentFactory` in `seocho/query/agent_factory.py`
5. `AgentStateMachine` in `runtime/agent_state.py`

Rules:

1. These are internal orchestration seams, not new root-level public APIs.
2. `seocho/client.py` remains the public facade while helpers move behind it.
3. `seocho/index/*` and `seocho/query/*` remain the canonical engine owners.
4. `runtime/*` may compose these seams but should not replace canonical engine
   ownership.
5. `extraction/*` remains shim or legacy batch surface only.

## Consequences

Positive:

- future facade decomposition has a stable destination for moved logic
- observability hooks become easier to add without widening public APIs
- runtime/local behavior can share internal orchestration contracts

Negative:

- one more layer of indirection for contributors to learn
- some seams are initially additive and only partially wired until later
  refactor slices land

## Implementation Notes

- design doc: `docs/INTERNAL_CLASS_DESIGN.md`
- ownership doc: `docs/MODULE_OWNERSHIP_MAP.md`
- first wiring target: local ingest path via `IngestionFacade`
