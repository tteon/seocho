# Runtime Shell Validation

Use this reference when working on `extraction/ -> runtime/` migration slices.

## Canonical Runtime Shell

Current canonical deployment-shell modules:

- `runtime/agent_server.py`
- `runtime/server_runtime.py`
- `runtime/policy.py`
- `runtime/public_memory_api.py`
- `runtime/runtime_ingest.py`

Compatibility aliases still intentionally exist under `extraction/`.

## Active Docs That Should Prefer `runtime/*`

- `README.md`
- `docs/WORKFLOW.md`
- `docs/AGENT_DEVELOPMENT.md`
- `docs/ARCHITECTURE.md`
- `docs/RUNTIME_PACKAGE_MIGRATION.md`

Historical ADRs and archive docs may still mention old paths. Do not churn them
unless the slice explicitly targets historical cleanup.

## Fast Validation

If the slice touches runtime shell paths, run:

```bash
bash scripts/ci/check-runtime-shell-contract.sh
```

Then run the smallest focused suites that cover the moved module.

## Basic CI Trigger

If the change touches repo-owned runtime shell files, runtime shell tests, or
their current docs/CI contract, also run:

```bash
bash scripts/ci/run_basic_ci.sh
```

## Typical Landing Checklist

1. canonical `runtime/*` owner created or updated
2. `extraction/*` alias preserved
3. repo-owned tests updated
4. active docs updated
5. ADR + `DECISION_LOG` updated
6. `.beads` task updated and closed
