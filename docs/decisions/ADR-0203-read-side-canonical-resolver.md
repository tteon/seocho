# ADR-0203: Read-side canonical resolver via a name-alias index (structured runtime, D2)

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-t28 / seocho-zfe (the read-time resolution contribution)
- Related: ADR-0160/0162/0183 (interning), ADR-0187 (text2cypher grounding),
  the multi-agent-flow review (blocker #3: shared pool is write-only)

## Context

The multi-agent-flow review's blocker #3 (verified genuine against origin/main, and long
known as seocho-t28): the shared intern pool is **write-only** on the read path. The write
side interns an entity under its *composite* identity `label|name|company|year`
(`index/identity.py`); a read only has a bare mention and cannot rebuild that composite, so
`resolve_mentions` — which reconstructs a single-key identity — **always missed multi-key
entities**. Cross-source made it worse: each source's own label/keys produce different
composite ids for the same real entity, so the pool fragments. The "sub-queries resolve one
string to ONE canonical node" thesis was unbacked because reads never used the pool.

## Decision

Add a source-agnostic **name-alias index** to the shared pool and consume it on the read
path (D2 of the multi-agent-flow redesign):

- `SharedInternTable.alias(ws, name, canonical_id)` records `(workspace, normalized-name) ->
  {canonical_id, ...}` as a **MULTIMAP** — homonyms accumulate as a SET, they do not
  overwrite. `candidates(ws, name)` returns them sorted; `resolve_one(ws, name)` returns the
  single id ONLY when unambiguous (a homonym returns `""` — never a silent guess).
- `apply_identity_keys` registers the alias (`name -> canonical`) right after interning the
  composite identity — additive, guarded by `hasattr(..., "alias")`, no behaviour change to
  the composite interning itself.
- `resolve_mentions` tries the single-key path first (which already agreed with the write),
  then falls back to `resolve_one` — so a bare mention now lands on the SAME canonical id the
  writer created, for multi-key entities, when unambiguous.

## Consequences

- **t28 closed for unambiguous multi-key entities**: a read finds the write's canonical by
  bare name (tested). The shared pool is now used on the read side — the intern organ becomes
  mechanism-true (shared-canonical resolution vs name/vector), as the arm×organ matrix needs.
- **Homonyms are surfaced, not collapsed**: `candidates` returns >1 for "Total Revenue"
  across PTC and Tesla; `resolve_one` refuses to guess. This is the honest boundary1 posture —
  context-disambiguation of homonym candidates is the next step (zfe-2), not a silent pick.
- Workspace-scoped: one tenant's names never appear as another's candidates (tested).
- 17 new tests; identity / intern / grounding suites green (39 together).

## Deferred / flagged
- **Cross-source CONVERGENCE** (two sources' fragments → one canonical) is NOT yet fixed —
  D2 only makes reads *find* and *surface* the fragments. Convergence needs a source-agnostic
  write identity or a reconciliation pass (next D2 sub-step).
- **Review #6 sub-claim to verify in D3/indexing**: the composite id carries no workspace
  component, so if the graph write MERGEs on `id` alone, two tenants' identical entity could
  merge onto one physical node. The intern TABLE and the `_workspace_id` read filter isolate
  correctly; the graph-MERGE key must be verified (and scoped by workspace if not already)
  when the single-federated-graph indexing (D3) lands.
