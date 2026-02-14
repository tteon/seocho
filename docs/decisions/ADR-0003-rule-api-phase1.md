# ADR-0003: Rule API Phase 1

- Status: Accepted
- Date: 2026-02-15
- Deciders: SEOCHO team

## Context

SHACL-like rule inference/validation existed as library logic but was not
exposed as a stable API surface.

## Decision

Expose first-class rule APIs:

- `POST /rules/infer`
- `POST /rules/validate`

and guard them with runtime policy actions:

- `infer_rules`
- `validate_rules`

## Scope

- add request/response contracts in `extraction/rule_api.py`
- support provided rule profile or inferred-on-demand validation
- include workspace-aware request contract

## Consequences

Positive:

- frontend and external clients can use rule operations directly
- decouples rule lifecycle from full extraction pipeline runs

Trade-offs:

- endpoint-level role assignment is still coarse (role/action)
- persistence/versioning of rule profiles is deferred to next phase
