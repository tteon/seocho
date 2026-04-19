---
name: "refactor-pr"
description: "Prepare one small, reviewable SEOCHO refactor draft PR. Trigger for local Codex CLI automation when the goal is one maintainability improvement with no intended behavior change, focused validation, and tightly bounded scope."
---

# Refactor PR

Use this skill for one bounded refactor lane change in SEOCHO.

The goal is one maintainability improvement with no intended product behavior
change.

## Allowed Scope

Pick at most one cohesive improvement from these categories:

- duplicate code reduction
- clearer module boundaries
- testability improvements
- small CI or contributor workflow hardening

## Avoid

Do not use this workflow for:

- intended product behavior changes
- broad renaming campaigns
- architecture rewrites
- speculative cleanup
- generated artifact commits

## Required Workflow

1. Read `AGENTS.md`, `README.md`, `docs/WORKFLOW.md`, and the latest relevant
   decision docs before editing.
2. Choose exactly one bounded refactor.
3. Keep the diff small enough for a quick draft PR review.
4. Run focused validation for the touched behavior only.
5. Do not merge or push directly to `main`.

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
