# ADR-0204: Cross-source canonical convergence (structured runtime, D2-2)

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-zfe (cross-source half of the read-time resolution contribution)
- Related: ADR-0203 (read-side resolver), the multi-agent-flow review (#3 cross-source
  fragmentation), the enterprise-rag cross-source thesis

## Context

ADR-0203 made reads *find* interned entities by name, but it also *surfaced* the
cross-source fragmentation the review flagged (#3): the same real entity written from two
sources gets two composite ids because the id is label-prefixed
(`company|acme` vs `organization|acme`), so a cross-source join lands on two fragments, not
one node. The "resolve one string to ONE canonical node" thesis needs the fragments to
actually converge.

## Decision

**Opt-in, source-agnostic canonical address for globally-name-unique types.**

- `NodeDef.cross_source_unique: bool = False` — the modeler declares which types are the
  same real entity across sources when their name matches (e.g. Company / Organization /
  Person), and which are not (homonym-prone metrics keep `identity_keys`). Default False →
  no behaviour change; roundtrips through `to_dict`/`from_dict`.
- In `apply_identity_keys`, a `cross_source_unique` node gets a **label-free, source-agnostic
  canonical id** `~xs|<normalized-name>` instead of the label-prefixed composite. Both
  sources compute the SAME id → both graph nodes MERGE to ONE physical node → the
  cross-source join lands on one node. This is *physical* convergence at write time, not a
  read-time remap.
- Safe by construction: name-merge only fires for types the modeler declared globally
  name-unique, so it never fuses genuinely-distinct types sharing a name (Apple the company
  vs the fruit) or homonym-prone metrics (which stay on `identity_keys` and are surfaced as
  candidates, never merged).
- `SharedInternTable.reconcile(ws, name)` + a per-workspace union-find (`_find`, path-
  compressed) remains as the **residual/explicit** reconciliation tool — for fragments not
  caught at write (e.g. a concurrent race, or a post-hoc same-as declaration). It unions the
  candidates to one deterministic representative; `candidates`/`resolve_one` return through
  it. (This is a read-level remap; physically re-merging already-written fragments is a
  re-projection / D3 concern.)

## Consequences

- Cross-source convergence is real and physical for declared types: "Acme Corp" from a jira
  Company and a confluence Organization → one node `~xs|acme corp`, `resolve_one` returns it
  (tested). The intern organ's cross-source claim is now backed, not asserted.
- Homonym-prone types are untouched — distinct composite ids, candidates surfaced, never
  fused (tested). The two policies (name-unique convergence vs identity-key separation) are
  cleanly split by an explicit ontology declaration.
- 6 new tests; ontology / identity / intern suites green (332 together). Opt-in default off.

## Note for D3 (single federated-graph indexing)
The `~xs|` id is workspace-agnostic in string form (content-addressed); isolation holds via
the intern table's `(workspace, …)` keys and the `_workspace_id` read filter. When D3 indexes
all sources into one graph, verify the graph MERGE is scoped so two tenants' identical `~xs|`
entity never share a physical node (review #6) — carry `_workspace_id` on the node and MERGE
on `(id, _workspace_id)`.
