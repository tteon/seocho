---
name: owlready-boundary
description: Ensure Owlready2 stays in the offline ontology governance path per CLAUDE.md §6.3 — forbidden in request hot path (runtime endpoints, seocho.session, agent execution). Invoke when touching any file that imports owlready2, or when adding ontology processing to runtime/, seocho/session.py, or HTTP endpoint handlers.
---

# owlready-boundary

Purpose: keep Owlready2's heavy reasoning confined to the offline governance path. Owlready2 reasoning in a request handler adds multi-second latency that defeats the SDK's agent-first design and blocks the runtime trace contract.

Ground contract: `CLAUDE.md` §1 (Owlready2 offline path only), §6.3 (Owlready2 Boundary — allowed offline, forbidden hot path), `AGENTS.md` §2, §5.

## When to invoke

- Any edit to a file importing `owlready2` (`import owlready2`, `from owlready2 import ...`)
- Any edit to a request handler in `runtime/`, `seocho/http_runtime.py`, `semantic/main.py`, `evaluation/server.py`, or `extraction/rule_api.py`
- Any edit to `runtime/policy.py` (pre-existing suspicious case — see "Known cases" below)
- Before landing a PR with `area-ontology` or `kind-feature` touching ontology

## Allowed vs forbidden

**Allowed (offline path):**
- `extraction/ontology_hints_builder.py` — offline hint construction
- `scripts/ontology/build_ontology_hints.py` — CLI tool
- `seocho/ontology_governance.py` — governance/validation flow
- `seocho/cli.py` — local command-line entry
- Test files: `seocho/tests/test_ontology_governance.py`

**Forbidden (hot path):**
- `runtime/agent_server.py` and anything it imports synchronously on request dispatch
- `seocho/session.py` — session is request-scoped
- `seocho/client.py` — synchronous client surface
- `seocho/store/*` — storage operations
- `seocho/query/*` — query execution
- Any `@app.{get,post,put,delete,patch}` decorated function or its synchronous callees
- `extraction/agents_runtime.py` — single canonical Agents SDK execution entry (§15)

## Procedure

### 1. Enumerate current Owlready2 surface

```bash
rg -l 'owlready2' --type py
```

Known current surface (as of 2026-04-16):

```
runtime/policy.py                       ← SUSPICIOUS — investigate (see below)
seocho/cli.py                            OK (CLI offline)
seocho/ontology_governance.py            OK (offline governance)
extraction/ontology_hints_builder.py     OK (offline hints)
scripts/ontology/build_ontology_hints.py OK (script)
seocho/tests/test_ontology_governance.py OK (test)
pyproject.toml / ADRs / DECISION_LOG     OK (declarations, not imports)
```

### 2. Investigate each hit

For each file returning from the grep:

```bash
rg -n 'import owlready2|from owlready2' <file>
```

For the file, determine:
- **Import path**: is `owlready2` imported at module load, inside a function, or lazily?
- **Caller path**: does any `runtime/*`, `seocho/session.py`, or HTTP handler transitively call into this file?

Use `mcp__serena__find_referencing_symbols` on the entry-point symbol to trace callers. If any caller is in the forbidden list above → violation.

### 3. Check for reasoning invocations

Owlready2 reasoning calls (expensive):
- `sync_reasoner()`, `sync_reasoner_pellet()`, `sync_reasoner_hermit()`
- `.save()` calls with `format="rdfxml"` that trigger inference
- World-level `.reason()`

Grep:

```bash
rg -n 'sync_reasoner|\.reason\(|World\(.*rdf' <touched files>
```

Any hit reachable from a request handler is a §6.3 violation.

### 4. Check for lazy / late imports

Sometimes a file is "forbidden" but imports Owlready2 lazily inside a function that is only called from offline paths. This is acceptable but fragile. Flag as `WARN` and add a comment at the import site explaining the offline-only invariant.

### 5. Investigate `runtime/policy.py` (existing)

This file appears in the `owlready2` grep and lives under `runtime/`. Before declaring it a violation, check:

```bash
rg -n 'owlready2' runtime/policy.py
```

Read the surrounding context. Likely outcomes:
- Used only for compile-time ontology validation during server startup → OK
- Used in a request-path policy check → §6.3 violation; file a `bd` issue with `sev-high`, `impact-high`

Do not silently fix this in an unrelated refactor — the runtime policy module is security-critical and needs its own review envelope.

### 6. Report

```
OK     seocho/ontology_governance.py:12    offline governance module
OK     extraction/ontology_hints_builder.py:8  offline hints
WARN   runtime/policy.py:45                 import at module load — confirm startup-only, not request-path
FAIL   runtime/new_endpoint.py:22           imports owlready2, called from POST /foo
```

## What to do with findings

- **FAIL** (Owlready2 reachable from request handler): block the change. Move the logic offline or feed the runtime a pre-computed artifact.
- **WARN** (existing gray-zone import like `runtime/policy.py`): file a separate `bd` investigation issue; do not fold into current work.
- **OK**: record and move on.

## Design guidance for new ontology work

If a feature needs ontology-derived information at request time:

1. Pre-compute the artifact offline (CLI or batch job in `scripts/ontology/` or `extraction/ontology_*`)
2. Persist it (disk, Postgres, or graph node) with a version / hash
3. Load the pre-computed artifact at server startup or cache it per workspace
4. Request handlers consume the artifact — never re-run Owlready2

This pattern is the intent behind `extraction/ontology_hints_builder.py` (offline) → runtime consumes hints.
