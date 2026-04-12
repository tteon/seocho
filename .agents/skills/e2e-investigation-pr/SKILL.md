---
name: "e2e-investigation-pr"
description: "Prepare one small, reviewable SEOCHO E2E investigation draft PR. Trigger for local Codex CLI automation when a concrete runtime or smoke failure has to be reproduced, narrowed, covered with focused regression validation, and fixed with the smallest viable change."
---

# E2E Investigation PR

Use this skill for one bounded E2E investigation lane change in SEOCHO.

The goal is to reproduce one concrete failure, prove it with narrow validation,
and apply the smallest viable fix.

## Allowed Scope

Pick at most one cohesive improvement from these categories:

- reproduce a smoke or E2E failure
- add focused regression coverage for the exact failure
- fix the smallest concrete root cause
- tighten operational guidance tied to the fixed failure

## Avoid

Do not use this workflow for:

- broad cleanup during incident work
- speculative fixes without reproduction
- multi-issue sweeps
- broad architecture redesign
- generated artifact commits

## Required Workflow

1. Read `AGENTS.md`, `README.md`, `docs/WORKFLOW.md`, and the latest relevant
   decision docs before editing.
2. Reproduce exactly one concrete failure first.
3. Add the smallest validation that proves the failure and the fix.
4. Keep the implementation narrow and easy to review.
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
