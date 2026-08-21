# ADR-0164: ablation A2 — isolation leak rate, enforcement OFF vs ON

Date: 2026-08-15 · Status: accepted (measurement record) · seocho-76k

## Context

Ablation row A2 of the OS study (wiki/os-ablation-study-design.md). The
isolation subsystem's axis: a session scoped to tenant `acme` must never return
another tenant's rows, even under adversarial or careless Cypher. We measure the
cross-tenant **leak count** with the governed enforcement OFF (a bare agent's
raw graph tool) vs ON (the OS path: `enforce_read_workspace_scope` + workspace
pin, PR #501/#512). This turns the unit-test attack shapes into a measured
before/after on a real graph.

## Method

`scripts/agentos/ablation_a2_isolation.py`, live DozerDB (`graphrag-neo4j`).
Two-tenant node set in the default database: 3 nodes `_workspace_id=acme`, 3
`_workspace_id=globex` (unique probe label, deleted after). Each attack nominally
scopes to `$workspace_id = acme`; a **leak** is any returned row whose
`_workspace_id != acme`. OFF runs the raw Cypher (token resolves, no gate); ON
runs the enforcement gate first — blocked → 0, passed → run pinned and count.
Floor = OFF (ungoverned); ceiling = a perfectly-scoped query (0 by construction),
included as the `properly_scoped` control to prove the gate does not over-block.

## Result

| attack | OFF leaked rows | ON leaked rows | ON outcome |
|---|---|---|---|
| unscoped_all (`MATCH (n) RETURN n`) | 3 | 0 | blocked: filter_missing |
| or_true | 3 | 0 | blocked: widening_tautology |
| or_1eq1 | 3 | 0 | blocked: widening_tautology |
| wrong_node_binding (`(n),(m) WHERE m…RETURN n`) | 9 | 0 | blocked: unbound_return |
| comment_smuggle (`… /* $workspace_id */`) | 3 | 0 | blocked: filter_missing |
| **properly_scoped** (control) | 0 | 0 | passed → 3 acme rows |
| **total** | **21 (5/6 attacks leak)** | **0 (0/6)** | — |

- **OFF leaks 21 cross-tenant rows across 5 of 6 attacks.** `wrong_node_binding`
  leaks the most (9) — the cartesian `(n),(m)` returns `n` from both tenants
  while only `m` is scoped, exactly the class the token-presence check missed
  before binding verification (#512).
- **ON leaks 0.** Every attack is refused with a specific reason; the legitimate
  `properly_scoped` control passes and returns only the 3 `acme` rows — the gate
  blocks attacks without over-blocking honest queries (no false positives).

## Consequences

- A2 is a clean ablation row: isolation enforcement converts 21 leaked rows (5
  attacks) into 0, on a real two-tenant graph, with the control proving no
  over-block. Feeds Level-2 of the OS ablation (seocho-5ny) and F2/F3 figures.
- Honest scope carries over from ADR-0157: this validates the *shipped*
  enforcement (blocklist + binding verification), which is defense-in-depth, not
  a hardware boundary. The sound endgame (per-workspace databases) would make ON
  = 0 a structural guarantee rather than a checked one; A2 would then re-run as
  the "hardened isolation" arm (seocho-5zz remainder).
