# ADR-0004: Rule Profile Lifecycle and Cypher Export

- Status: Accepted
- Date: 2026-02-15
- Deciders: SEOCHO team

## Context

Rule inference/validation APIs existed, but clients needed:

- profile persistence for reuse and versioning
- conversion path to DozerDB-native constraints

## Decision

Add the following APIs:

- `POST /rules/profiles`
- `GET /rules/profiles`
- `GET /rules/profiles/{profile_id}`
- `POST /rules/export/cypher`

Implementation choices:

- profile storage: filesystem JSON under `outputs/rule_profiles/<workspace_id>/`
- export mapping: `required` rules -> `IS NOT NULL` constraints
- unsupported mappings (`datatype`, `enum`, `range`) are returned explicitly

## Consequences

Positive:

- frontend can persist and reuse rules without rerunning full pipeline
- DB constraint rollout can be automated from rule profiles

Trade-offs:

- filesystem storage is single-node and not a long-term registry
- partial mapping only; advanced constraints remain app-level checks
