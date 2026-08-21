# ADR-0209: text2cypher repair loop — feed guardrail rejections back for a retry

- Status: accepted
- Date: 2026-08-16
- Tickets: seocho-ia4.13 (text2cypher)
- Related: ADR-0208 (grounded text2cypher), ADR-0189 (profile gate)

## Context

ADR-0208 made the structured engine's generator conformant-by-prompt, and named a repair loop
as the natural follow-up: an LLM can still emit non-conformant Cypher on a harder schema, and
without a retry the governed arm abstains on an answerable question — the Plane-2 confound
(governed looks worse for a generator defect, not for governance).

## Decision

`StructuredQueryOrchestrator` gains a `repair_budget` (default 0 = off; the local engine sets
1). When the guardrail rejects the generated Cypher, the orchestrator feeds the **violation
reasons + the rejected query** back to the generator (`generate_grounded_cypher(..., feedback=)`)
and retries, up to the budget, before abstaining. `_call_gen` passes `feedback=` when the
generator accepts it and falls back for a 2-arg test double, so injected generators stay
compatible. `StructuredQueryResult.repair_attempts` records how many retries were spent.

## Consequences

- A fixable non-conformance (a missing workspace scope on one node, an inlined literal) is
  repaired instead of forcing an abstain, so the governed arm's answer/abstain reflects
  GOVERNANCE, not a one-shot generator miss — removing the confound before the Plane-2 A/B.
- Bounded + honest: after `repair_budget` retries a still-rejected query abstains (a rejection
  is still reported as a rejection, ADR-0205 D5); `repair_attempts` is measurable per request.
- 3 tests: repair recovers a rejected query (feedback seen, attempts=1, executes), no-budget =
  no-repair, gives-up-after-budget. Orchestrator/engine/generator suites green (16).
