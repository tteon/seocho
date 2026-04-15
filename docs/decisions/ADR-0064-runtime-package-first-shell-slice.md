# ADR-0064: Runtime Package First Shell Slice

## Status

Accepted

## Context

`ADR-0062` chose `runtime/` as the long-term deployment-shell package name, but
the repository still kept the implementation under `extraction/`. That left the
rename at the planning layer only:

- current code ownership still looked like `extraction/*`
- flat imports such as `import agent_server` and `import server_runtime` were
  still the de facto runtime entrypoints
- runtime/package boundary discussions could not point to a landed canonical
  module path

The first concrete rename slice needs to move thin shell modules without
touching the heavier runtime-ingest or extraction-only helpers.

## Decision

Introduce `runtime/` as the canonical deployment-shell package and move the
following modules there:

- `runtime/agent_server.py`
- `runtime/server_runtime.py`
- `runtime/policy.py`
- `runtime/public_memory_api.py`

Keep the historical flat `extraction/*` import surface working through
module-alias compatibility shims:

- `extraction/agent_server.py`
- `extraction/server_runtime.py`
- `extraction/policy.py`
- `extraction/public_memory_api.py`

Use a temporary bootstrap in `runtime/__init__.py` to keep the remaining
flat-module extraction helpers importable while the shell migration is only
partially complete.

## Consequences

### Positive

- `runtime/` now exists as a real code owner, not only a future plan
- current docs can point to the actual canonical shell path
- downstream code can start importing `runtime.*` immediately
- flat `extraction/*` imports keep working for tests and existing runtime entrypoints

### Trade-offs

- `runtime/__init__.py` currently bootstraps the old `extraction/` flat-module
  path, so the shell rename is not fully clean yet
- heavy orchestration and extraction-only modules still remain outside `runtime/`
- shim cleanup and explicit package-import migration are still follow-up work

## Follow-up

- move additional runtime-only modules under `runtime/` as they become thin
  enough to migrate safely
- reduce dependence on the extraction flat-module path bootstrap
- eventually deprecate or remove the `extraction/*` compatibility aliases
