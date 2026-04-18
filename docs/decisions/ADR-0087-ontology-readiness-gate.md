# ADR-0087: Ontology Readiness Gate on Rule Profile Promotion

Date: 2026-04-18
Status: Accepted

## Context

SEOCHO's thesis for closed-network finance deployments is to lower model
dependency by making the ontology carry the quality bar. `POST /rules/assess`
already computes a `practical_readiness` verdict with status in
`{ready, caution, blocked}` and a detailed violation breakdown, but no caller
consumed that verdict. A blocked assessment was informational only.

This meant the ontology functioned as *context* for LLM prompting, not as a
*gate* on governance actions. Rule profiles could be promoted even when
readiness was blocked, which directly contradicts the thesis (see
`docs/PHILOSOPHY.md` §24 and `CLAUDE.md` §14).

## Decision

Introduce an opt-in readiness gate on `POST /rules/profiles`:

- `RuleProfileCreateRequest` gains `validation_graph: Optional[Dict]` and
  `acknowledge_blocked_readiness: bool = False`.
- When `validation_graph` is supplied, the service re-runs the same readiness
  computation used by `/rules/assess` against the submitted rule profile.
- If the verdict is `blocked`, the service raises `ReadinessBlockedError`,
  which the runtime handler translates to HTTP 409 Conflict with a structured
  body: `{error: "readiness_blocked", message, readiness: {...}}`.
- `acknowledge_blocked_readiness=True` allows promotion even on a blocked
  verdict; the verdict is echoed on the response for auditing. This preserves
  operator escape hatches without silent bypass.
- When `validation_graph` is omitted, behavior is unchanged.

The gate is deliberately opt-in rather than always-on. An always-on gate would
require per-workspace reference-graph management, which is a separate design
discussion outside this slice.

## Consequences

- Rule profile promotion is the first governance surface where the ontology
  acts as an enforcement gate rather than advisory context.
- Backward-compatible: existing callers that omit `validation_graph` see no
  behavior change.
- `acknowledge_blocked_readiness` keeps overrides explicit and auditable per
  the project's "explicit opt-in over magic" principle.
- `POST /semantic/artifacts/{id}/approve` and `POST /platform/ingest/raw` are
  also candidate gate surfaces but are deferred: the artifact approval path
  needs a design decision on how to project `shacl_candidate` into a rule
  profile for assessment, and the ingest path needs a reference-graph concept
  before it can be gated deterministically. Both will be addressed in
  follow-up beads under seocho-5vm.

## Related Documents

- `CLAUDE.md` §14 (Philosophy Alignment) and §6.2 (Rules API Surface)
- `docs/PHILOSOPHY.md` §24
- `docs/decisions/ADR-0003-rule-api-phase1.md`
- `docs/decisions/ADR-0004-rule-profile-lifecycle-and-cypher-export.md`
- Bead: seocho-5vm
