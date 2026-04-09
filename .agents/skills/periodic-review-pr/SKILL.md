---
name: "periodic-review-pr"
description: "Prepare one reviewable SEOCHO improvement PR for scheduled repository review passes. Trigger for weekly or manual Codex automation that should inspect the repo, choose one bounded refactor or small developer-facing improvement, apply it, validate it, and return a PR-ready summary."
---

# Periodic Review PR

Use this skill for broader recurring repository review than the daily
maintenance pass, while still keeping the resulting PR safe and reviewable.

The goal is not open-ended product invention. The goal is one cohesive draft PR
that improves the repository in a way a maintainer can realistically review and
merge.

## Allowed Scope

Pick at most one cohesive improvement from these categories:

- focused refactor with no intended behavior change
- small developer-facing SDK or CLI improvement
- targeted regression coverage for existing or recently added behavior
- packaging, release, or CI hardening
- docs updates that clarify implemented behavior or public SDK usage

## Avoid

Do not use this workflow for:

- large feature work
- multi-area refactors
- speculative roadmap shifts
- broad dependency upgrades
- generated artifact commits
- changes requiring hidden credentials or non-repo internet access inside Codex

## Required Workflow

1. Read `AGENTS.md`, `README.md`, `docs/WORKFLOW.md`, and the latest relevant
   decision docs before editing.
2. Check the current repository state and avoid overwriting unrelated local
   changes.
3. Choose exactly one bounded improvement.
4. If the best idea is larger than a small PR, do not implement it in the
   scheduled workflow.
5. Keep the diff small enough for a draft PR review.
6. Run focused validation for the touched behavior only.
7. Do not commit, push, or merge.

## Graph-RAG Work

If the improvement touches semantic retrieval or graph-grounded answering,
align with `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`.

Prefer contract hardening, targeted tests, or ergonomic API improvements over
broad runtime changes in the scheduled workflow.

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
