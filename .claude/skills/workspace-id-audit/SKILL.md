---
name: workspace-id-audit
description: Audit runtime endpoints and write/compute paths for workspace_id propagation per CLAUDE.md §6.1. Invoke after adding or modifying HTTP routes in runtime/, semantic/, seocho/http_runtime.py, or evaluation/server.py; or when refactor-safety flags runtime hot-path changes; or before landing a runtime contract change.
---

# workspace-id-audit

Purpose: enforce the single-tenant MVP contract — every runtime write/compute endpoint accepts and propagates `workspace_id`. This is a **contract**, not a best practice. Violating it breaks routing, policy checks, and trace vendor-neutrality.

Ground contract: `CLAUDE.md` §1 (single-tenant MVP with `workspace_id` end-to-end), §6.1 (Workspace-Aware Contracts), §9 (Observability — include `workspace_id` in trace metadata), `AGENTS.md` §2, §5.

## When to invoke

- After adding or modifying any `@app.{get,post,put,delete,patch}` or `@router.*` decorator in the runtime surface
- Before landing a PR with `area-backend` + `kind-feature`
- When `refactor-safety` classifies the touch set as runtime hot path
- When adding tracing or observability to a new code path

## Runtime surface to audit

Endpoint files (grep `@app\.` or `@router\.` in these):

- `runtime/agent_server.py` — primary runtime
- `runtime/public_memory_api.py`
- `seocho/http_runtime.py`
- `semantic/main.py`
- `evaluation/server.py`
- `extraction/rule_api.py` — rules surface (see CLAUDE.md §6.2)
- `extraction/semantic_artifact_api.py`

Non-endpoint write/compute paths that also require `workspace_id`:

- `runtime/runtime_ingest.py`, `runtime/memory_service.py`
- `seocho/store/graph.py` (database selection)
- `seocho/session.py` (session context)
- `extraction/agents_runtime.py` (the single canonical entry point for Agents SDK execution — CLAUDE.md §15)

## Procedure

### 1. Enumerate endpoints in the touch set

```bash
rg -n "@(app|router)\.(get|post|put|delete|patch)\(" runtime/ semantic/ evaluation/ seocho/http_runtime.py extraction/rule_api.py extraction/semantic_artifact_api.py
```

For each hit, read the decorated function signature.

### 2. Check each endpoint signature

An endpoint is compliant if **either**:

- The function has a `workspace_id: str` parameter (path, query, header, or body field), **or**
- The endpoint is explicitly read-only and workspace-agnostic (e.g., `/health`, `/version`, `/databases` listing that does not leak per-workspace data)

A common compliant pattern:

```python
@app.post("/rules/validate")
async def validate(req: ValidateRequest, workspace_id: str = Header(..., alias="X-Workspace-Id")):
    _require_workspace(workspace_id)   # policy check
    ...
```

Non-compliant examples:
- `workspace_id` passed but never validated
- `workspace_id` read from a global / env var / request state (breaks multi-request isolation)
- Write endpoint with no workspace scoping at all

### 3. Confirm policy validation

Each endpoint with `workspace_id` should invoke policy validation. Grep:

```bash
rg -n "workspace_id" runtime/policy.py seocho/policy* extraction/policy.py 2>/dev/null
```

Check the touched endpoint actually calls the validator (not just accepts the param).

### 4. Check trace metadata

If the endpoint invokes tracing (`@track`, `SessionTrace`, or explicit JSONL write), `workspace_id` must appear in the trace metadata. Grep:

```bash
rg -n '"workspace_id"|workspace_id=' <touched file>
```

`CLAUDE.md` §9 makes this part of the observability contract, not optional.

### 5. Verify non-endpoint write paths

For any modified file in `runtime/`, `seocho/store/`, `seocho/session.py`, or `extraction/agents_runtime.py`, confirm public functions performing writes or agent-execution accept `workspace_id` and pass it through.

### 6. Report

Produce a concise list:

```
OK     runtime/agent_server.py:142  POST /chat/send           workspace_id in body, validated, traced
OK     runtime/agent_server.py:198  GET  /sessions/{sid}      read-only, workspace_id from session
MISS   runtime/agent_server.py:231  POST /agents/debate       workspace_id accepted but not validated
MISS   evaluation/server.py:88       POST /eval/run            no workspace_id parameter
```

## What to do with findings

- **MISS on endpoint you just added** — fix before committing. This is why the audit exists.
- **MISS on pre-existing endpoint** — file a `bd` issue with `sev-high`, `impact-high`, `urgency-this_sprint`, `kind-issue`. Do not silently fix in a refactor PR — that hides the regression window.
- **OK with note** — record any edge cases (e.g., read-only by design) so future audits don't re-flag.

## Notes

- `extraction/` is 168+ `workspace_id` references already — coverage there is mature. Pay the most attention to `runtime/` and new files.
- `agents_runtime.py` is the single agent execution entry point per §15 — any feature module calling `Runner.run` directly is a separate violation to flag.
