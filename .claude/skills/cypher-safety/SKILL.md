---
name: cypher-safety
description: Review Cypher queries for DozerDB/Neo4j safety per CLAUDE.md §8 — prefer elementId() over id(), validate dynamic labels/properties before interpolation, enforce database name pattern, keep query tools read-safe unless write is explicitly required. Invoke when editing files containing Cypher strings in seocho/store/, seocho/query/, runtime/, or extraction/semantic_query_flow.py.
---

# cypher-safety

Purpose: prevent silent corruption and injection in the DozerDB backend. Cypher is our primary graph language and runtime path — unsafe interpolation or deprecated ID usage accumulates as tech debt that is expensive to unwind later.

Ground contract: `CLAUDE.md` §1 (DozerDB backend), §8 (DozerDB/Graph Safety Rules), §13 (Reliability Notes — `apoc.*,n10s.*` scope, no wildcard unrestricted).

## When to invoke

- Editing any file containing `MATCH `, `MERGE `, `CREATE (`, `DETACH DELETE`, or raw Cypher strings
- Adding a new graph query pathway
- Reviewing a PR that touches `seocho/store/graph.py`, `seocho/query/*`, `runtime/memory_service.py`, `runtime/runtime_ingest.py`, `extraction/semantic_query_flow.py`, `extraction/graph_loader.py`

## Files where Cypher lives (known surface)

Runtime (hot path):
- `seocho/store/graph.py`
- `seocho/session.py`
- `seocho/query/semantic_agents.py`
- `seocho/query/cypher_validator.py` — validator itself; changes here have outsized blast radius
- `runtime/memory_service.py`
- `runtime/runtime_ingest.py`
- `extraction/semantic_query_flow.py`
- `seocho/ontology_context.py`

Offline / batch:
- `extraction/graph_loader.py`
- `seocho/ontology.py`

## Safety rules (from §8)

1. **Database names must pass registry validation** — pattern `^[a-z][a-z0-9]{2,62}$`, via `DatabaseRegistry` in `extraction/config.py`.
2. **Dynamic labels/properties must be validated before Cypher interpolation** — never f-string an untrusted label into a query.
3. **Query tools remain read-safe unless write mode is explicitly required** — default read-only.
4. **Prefer `elementId(...)` over deprecated `id(...)`** in query-time/runtime Cypher paths.

## Procedure

### 1. Enumerate Cypher strings in the touch set

```bash
# match strings spanning lines as well
rg -n 'MATCH |MERGE |CREATE \(|DETACH DELETE|CALL apoc\.|CALL n10s\.' <touched files>
```

Also check for Cypher inside triple-quoted strings and `textwrap.dedent` blocks.

### 2. Check for deprecated `id()`

```bash
rg -n '\bid\(' <touched files> | rg -v 'elementId\('
```

**Any hit inside a Cypher string is a violation** unless it's already wrapped (e.g., `elementId(n)` — `id(` appears as substring but the actual call is `elementId`). Distinguish:
- `WHERE id(n) = $x`  — violation, must be `WHERE elementId(n) = $x`
- `elementId(n)`      — OK
- Python `id()` builtin — OK (not inside Cypher string)

Known clean file: `seocho/ontology_context.py` uses Python `id()` builtin, not Cypher `id()`. Do not flag unless the grep hit is inside a raw-string query.

### 3. Check for dynamic label/property interpolation

Violations look like:

```python
query = f"MATCH (n:{label}) RETURN n"    # UNSAFE if label untrusted
query = f"MATCH (n) SET n.{prop} = $v"   # UNSAFE if prop untrusted
```

Safe forms:

```python
from seocho.query.cypher_validator import validate_label, validate_property
query = f"MATCH (n:{validate_label(label)}) RETURN n"
```

Or use parameterized node-label APIs where the driver supports them.

Grep:

```bash
rg -n 'f"[^"]*:\{|f"[^"]*\.\{|format\(.*\{label\}|format\(.*\{prop' <touched files>
```

For each hit: confirm the interpolated variable has passed through `validate_label`, `validate_property`, or an equivalent allowlist. If not → violation.

### 4. Check for database name validation

If the code selects a database dynamically:

```bash
rg -n 'driver\.session\(database=|\.session\(\s*database=' <touched files>
```

The `database` parameter must come from `DatabaseRegistry` or pass `^[a-z][a-z0-9]{2,62}$` validation. Raw user input is a violation.

### 5. Check read/write intent

If the Cypher is `MATCH ... RETURN` only, confirm the session is opened with `default_access_mode=neo4j.READ_ACCESS` or equivalent read-only. Mutating queries in a read-mode session will fail at runtime, but read queries in write-mode session are a subtler issue — they acquire write locks unnecessarily.

### 6. Check APOC/N10S scope

Per §13: DozerDB procedure privileges must stay scoped to `apoc.*,n10s.*`. Any new `CALL` to a different namespace (e.g., `gds.*`, `custom.*`) needs explicit review before merging — flag in report.

### 7. Report

```
OK     seocho/store/graph.py:88       elementId used, params only
WARN   extraction/semantic_query_flow.py:220  dynamic label without validate_label: "MATCH (n:{category})"
FAIL   runtime/runtime_ingest.py:142  id() in Cypher — replace with elementId()
WARN   seocho/session.py:301          database= from untrusted arg — route via DatabaseRegistry
```

## What to do with findings

- **FAIL** (deprecated `id()`, raw interpolation without validator): fix before committing
- **WARN** (read-mode not explicit, new `CALL` namespace): add a comment or file `bd` issue
- **OK**: no action; include in report for trail
