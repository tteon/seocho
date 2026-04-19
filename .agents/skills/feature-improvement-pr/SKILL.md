---
name: "feature-improvement-pr"
description: "Prepare one small, reviewable SEOCHO feature-improvement draft PR. Trigger for local Codex CLI automation when the goal is one developer-facing capability improvement with bounded scope, focused validation, and no broad architecture redesign."
---

# Feature Improvement PR

Use this skill for one bounded feature-improvement lane change in SEOCHO.

The goal is not roadmap expansion. The goal is one small developer-facing
improvement that a maintainer can review quickly.

## Allowed Scope

Pick at most one cohesive improvement from these categories:

- small SDK or CLI ergonomics improvement
- narrow developer-facing runtime capability
- docs updates tied to the implemented behavior
- focused regression coverage for an existing feature

## Avoid

Do not use this workflow for:

- multi-area feature work
- semantic retrieval redesign
- routing or ontology policy changes
- broad dependency upgrades
- generated artifact commits

## Required Workflow

1. Read `AGENTS.md`, `README.md`, `docs/WORKFLOW.md`, and the latest relevant
   decision docs before editing.
2. Choose exactly one bounded feature improvement.
3. Keep the diff small and easy to review.
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
