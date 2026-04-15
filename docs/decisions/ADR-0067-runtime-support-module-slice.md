# ADR-0067: Move Runtime Support Modules Under runtime/

Date: 2026-04-15
Status: Accepted

## Context

After introducing the canonical `runtime/` deployment-shell package, several
support modules still lived under `extraction/` even though they were runtime
HTTP/service concerns:

- readiness summary helpers
- request correlation middleware
- the memory-first runtime facade over ingest and semantic graph search

Keeping these modules under `extraction/` preserved historical naming drift and
required canonical runtime modules to keep importing deployment-shell support
from flat extraction-era paths.

## Decision

Move canonical ownership to:

- `runtime/agent_readiness.py`
- `runtime/middleware.py`
- `runtime/memory_service.py`

Keep compatibility aliases at:

- `extraction/agent_readiness.py`
- `extraction/middleware.py`
- `extraction/memory_service.py`

Update repo-owned tests, active docs, basic CI, and runtime-shell contract
checks to prefer the canonical `runtime/*` paths.

## Consequences

### Positive

- runtime shell ownership becomes more internally consistent
- active tests reinforce the new package boundary
- `extraction/` loses more deployment-shell responsibility without breaking
  existing import paths

### Negative

- compatibility aliases remain and still need a later deprecation/removal plan
- `runtime/__init__.py` still bootstraps historical `extraction/` flat-module
  imports for remaining runtime dependencies

## Out Of Scope

- moving database manager, graph connector, or semantic query compatibility
  modules
- removing any compatibility alias
- changing public API behavior

## Follow-up

- continue downstream import cleanup for remaining runtime-only helpers
- classify the remaining `extraction/` modules as extraction-only, runtime
  support, canonical SDK, or legacy compatibility
