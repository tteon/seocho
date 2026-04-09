---
name: "daily-maintenance-pr"
description: "Prepare one small, safe SEOCHO maintenance PR for scheduled Codex automation. Trigger for daily or ad hoc maintenance passes that should inspect the repo, choose one reviewable improvement, apply focused changes, run targeted validation, and return a PR-ready summary."
---

# Daily Maintenance PR

Use this skill for low-risk recurring repository maintenance in SEOCHO.

The goal is not broad product work. The goal is one small, reviewable PR that a
maintainer can inspect quickly.

## Allowed Scope

Pick at most one cohesive improvement from these categories:

- documentation clarity tied to existing behavior
- focused regression coverage for existing behavior
- small CI or workflow hardening
- low-risk runtime guardrail or configuration cleanup
- contributor workflow fixes that do not change product scope

## Avoid

Do not use this workflow for:

- large refactors
- multi-area feature work
- broad dependency upgrades
- sweeping formatting churn
- generated artifact commits
- changes that require hidden credentials or internet access inside Codex

## Required Workflow

1. Read `AGENTS.md`, `README.md`, `docs/WORKFLOW.md`, and the latest relevant
   decision docs before editing.
2. Check the current repository state and avoid overwriting unrelated local
   changes.
3. Choose exactly one bounded improvement.
4. Keep the diff small and easy to review.
5. Run focused validation for the touched behavior only.
6. Do not commit, push, or merge.

## Graph-RAG Work

If the daily maintenance item touches semantic retrieval or graph-grounded
answering, align with `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`.

Prefer small contract clarifications or targeted tests over broad pipeline
changes in the scheduled workflow.

## Final Response Format

Return a PR-ready summary in this shape:

```md
## Feature
- ...

## Why
- ...

## Design
- ...

## Expected Effect
- ...

## Impact Results
- ...

## Validation
- `...`

## Risks
- ...
```

If no safe isolated change exists, make no edits and say so explicitly under
the same headings.
