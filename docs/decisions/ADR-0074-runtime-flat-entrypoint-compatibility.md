# ADR-0074: Runtime Flat Entrypoint Compatibility

Date: 2026-04-15
Status: Accepted

## Context

SEOCHO is moving deployment-shell ownership from the historically overloaded
`extraction/` package to canonical `runtime/*` modules. The first migration
slices moved runtime modules and left `extraction/*` files as compatibility
aliases.

One compatibility seam remained under-specified: historical flat entrypoints
still start from the `extraction/` directory. In particular, the local compose
service still runs `uvicorn agent_server:app` from `/app`, where `/app` is the
mounted `extraction/` directory. A flat alias such as `agent_server.py` cannot
import `runtime.agent_server` unless repo-root modules are also importable.

## Decision

Keep `runtime/` as the canonical deployment-shell owner, but make flat
`extraction/*` aliases responsible for bootstrapping the repo root before
delegating to `runtime.*`.

Add `extraction/_runtime_alias.py` as the shared alias helper. Each flat
compatibility module imports that helper and calls
`alias_runtime_module(__name__, "runtime.<module>")`.

For the local compose stack, keep the service name `extraction-service` for now
but bind-mount `runtime/` and `seocho/` into `/app`. This keeps the existing
developer activation path working while runtime ownership continues to move out
of `extraction/`.

## Consequences

- Existing flat imports such as `import policy` and `uvicorn agent_server:app`
  remain valid during the staged rename.
- Canonical implementation ownership stays in `runtime/*`; no new business
  logic lands in `extraction/`.
- The local compose service can resolve both canonical runtime shell code and
  canonical SDK modules.
- The contract check now guards this seam so future runtime slices do not
  accidentally break legacy entrypoints before the service is renamed.

## Follow-Ups

- Rename or rebuild the compose service around a repo-root runtime image once
  downstream workflows no longer depend on the `extraction-service` name.
- Remove flat `extraction/*` aliases only after downstream imports and
  deployment entrypoints have migrated to `runtime.*`.

## Related Documents

- `docs/RUNTIME_PACKAGE_MIGRATION.md`
- `docs/WORKFLOW.md`
- `docs/ARCHITECTURE.md`
- `docs/decisions/ADR-0062-staged-runtime-package-rename.md`
- `docs/decisions/ADR-0064-runtime-package-first-shell-slice.md`
