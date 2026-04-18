# Runtime Package Migration

This document is the shipped plan for moving SEOCHO away from the historically
overloaded `extraction/` package name.

## Why This Exists

`extraction/` started as an ingestion-oriented package, but it accumulated:

- HTTP route wiring
- runtime policy checks
- readiness and health behavior
- runtime service composition
- compatibility shims for canonical `seocho/*` code

That name no longer matches the job.

## Target Boundary

Long-term package ownership should be:

- `seocho/`: canonical engine
- `runtime/`: deployment shell
- `extraction/`: extraction-only concerns or temporary compatibility wrappers

We are choosing `runtime/` as the long-term target package name.

Why `runtime/` instead of `server/`:

- it covers HTTP entrypoints plus deployment-only readiness and policy behavior
- it does not imply that every runtime concern is only an HTTP server concern
- it leaves room for local runtime composition without overloading `server/`

## Ownership Contract

## User-Facing Impact

This migration is intended to be invisible to end users and API consumers:

- endpoint paths stay the same
- request and response contracts stay the same
- runtime behavior should stay the same except for bug fixes

The main effect is engineering-facing:

- less SDK/runtime drift
- easier parity testing
- clearer docs and contributor entry points
- lower risk when moving more runtime shell code out of `extraction/`

### `seocho/` owns

- extraction/query/agent/rules/linking logic
- ontology context shaping
- deterministic graph contracts
- parity-tested execution behavior

### `runtime/` owns

- request validation
- policy and authorization
- workspace/database registry
- API route wiring
- deployment-specific health/readiness

## Staged Migration Plan

### Stage 0: Freeze New Logic

- no new canonical business logic lands in `extraction/`
- new business logic lands under `seocho/`
- `extraction/` changes must either:
  - call canonical `seocho/*` code, or
  - act as transport/compatibility shell

### Stage 1: Introduce Canonical Runtime Surface

- create `runtime/` as the new canonical deployment-shell package
- keep `extraction/` import paths working
- move thin wrappers first, not heavy orchestrators first

Current landed first slice:

- `runtime/agent_server.py`
- `runtime/agent_readiness.py`
- `runtime/middleware.py`
- `runtime/memory_service.py`
- `runtime/server_runtime.py`
- `runtime/policy.py`
- `runtime/public_memory_api.py`
- `runtime/runtime_ingest.py`
- `extraction/agent_server.py`, `extraction/agent_readiness.py`,
  `extraction/middleware.py`, `extraction/memory_service.py`,
  `extraction/server_runtime.py`, `extraction/policy.py`,
  `extraction/public_memory_api.py`, and `extraction/runtime_ingest.py`
  remain as compatibility aliases
- compatibility aliases use `extraction/_runtime_alias.py` so legacy flat
  entrypoints such as `uvicorn agent_server:app` can resolve canonical
  `runtime/*` modules when started from the `extraction/` directory
- the default local `extraction-service` compose path is now rebuilt from a
  repo-root image that bakes `extraction/`, `runtime/`, and `seocho/`
- explicit live bind mounts moved to `docker-compose.dev.yml` for development
  loops that want host-edit reflection instead of snapshot reproducibility

Target first modules:

- `runtime/agent_server.py`
- `runtime/agent_readiness.py`
- `runtime/middleware.py`
- `runtime/memory_service.py`
- `runtime/server_runtime.py`
- `runtime/policy.py`
- `runtime/public_memory_api.py`
- `runtime/runtime_ingest.py`

### Stage 2: Move Thin Runtime Shell Modules

Move the modules whose primary responsibility is transport or runtime
composition:

- route registration
- request/response contracts
- service composition
- runtime registry and readiness surfaces

Do not move batch ingest orchestration and historical extraction helpers in the
same slice.

### Stage 3: Separate Extraction-Only Concerns

After the shell moves, decide which remaining modules are truly extraction-only:

- batch ingest helpers
- raw material parsing
- dataset-specific ingest helpers
- legacy import adapters that still need to survive

At this point, `extraction/` should either:

- become extraction-only, or
- shrink to a compatibility namespace before removal

### Stage 4: Deprecate and Remove

- document import migrations
- emit deprecation warnings where appropriate
- close shim-removal tasks in `.beads`
- remove `extraction/` wrappers only after downstream imports are updated

## Merge Gates

Each migration slice must satisfy:

1. public import compatibility is preserved or explicitly documented
2. parity harness stays green where applicable
3. no new business logic is introduced in `extraction/`
4. route and policy behavior remain stable
5. ADR and architecture docs are updated
6. `bash scripts/ci/check-runtime-shell-contract.sh` passes for runtime-shell slices

## Benchmark Interaction Rule

Benchmarking should treat:

- SDK path as canonical engine quality
- runtime path as deployment overhead plus policy/transport cost

The package rename must not change this interpretation.
