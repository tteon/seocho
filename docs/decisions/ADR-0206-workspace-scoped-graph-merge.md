# ADR-0206: Workspace-scoped node/rel MERGE (review #6, D3 code-half)

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4 (structured runtime), the multi-agent-flow review (#6)
- Related: ADR-0204 (cross-source convergence — source-agnostic `~xs|` ids),
  ADR-0164 (workspace isolation)

## Context

`Neo4jGraphStore.write` MERGEd nodes on `id` alone (`MERGE (n:L {id: row.id})`) and matched
relationship endpoints on `id` alone. ADR-0204 made cross-source-unique entities share a
**source-agnostic id** (`~xs|<name>`) that is IDENTICAL across tenants for the same-named
entity. In a shared graph (the D3 single-federated-graph model, and any multi-workspace-in-
one-database deployment) that means two tenants' "Acme Corp" would MERGE onto **one physical
node** and a relationship could bridge two tenants' nodes — a cross-tenant collision (the
review's blocker #6). `_workspace_id` was written as a *property* but was not part of node
IDENTITY, so it could not prevent the merge.

## Decision

Make `_workspace_id` part of the write-time node identity and the relationship endpoint match:

- Node: `MERGE (n:L {id: row.id, _workspace_id: $ws})` (batch and single-row), with
  `ws=workspace_id` bound.
- Relationship endpoints: `MATCH (a:… {id: row.src, _workspace_id: $ws}), (b:… {id: row.tgt,
  _workspace_id: $ws})` so a relationship only ever connects nodes of the SAME tenant.

`write(...)` already stamps `props["_workspace_id"] = workspace_id` on every node, so existing
nodes carry the property and are still matched by the new key — **no migration break** for the
common case (single-tenant nodes all share `_workspace_id="default"`). Two tenants' identical
`id` now key to distinct `(id, _workspace_id)` nodes.

## Consequences

- Two tenants' identical entity id (source-agnostic or otherwise) never share a physical node,
  and no relationship bridges tenants — the shared-graph model (D3) is tenant-safe at the
  write path, and CLAUDE.md's "write paths must preserve workspace_id" is honoured in node
  IDENTITY, not just as a filterable property.
- 3 new tests assert the generated Cypher is workspace-scoped (node MERGE + both rel
  endpoints) and that two tenants bind distinct `ws`; the existing LWW writer test's endpoint
  assertion was updated to the new pattern. Graph/index/convergence suites green (24).
  (`test_graph_db_span`'s 3 failures are pre-existing on origin/main — a query-path fake
  missing `default_access_mode`, unrelated to this write-path change.)

## Note
This is the code-half of D3. The full single-federated-graph indexing (all sources into one
workspace-scoped graph, `_source_platform` stamped) + its live validation is the e2e; this
change makes that graph tenant-safe by construction.
