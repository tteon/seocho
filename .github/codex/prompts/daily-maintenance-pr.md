Use the repo-local skill `$daily-maintenance-pr`.

Task:

- Inspect the current SEOCHO repository and prepare one small, safe maintenance
  change that is ready for review as a pull request.
- Respect `AGENTS.md`, `README.md`, `docs/WORKFLOW.md`, and
  `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md` when relevant.

Scope constraints:

- Choose exactly one cohesive improvement.
- Prefer docs consistency, targeted regression tests, CI hardening, or small
  runtime guardrails.
- Do not attempt a broad feature, large refactor, dependency sweep, or
  multi-theme cleanup.
- Do not touch generated artifacts or unrelated dirty files.
- If no safe reviewable improvement exists, make no code changes.

Execution rules:

- Read the current repo state first.
- Keep edits minimal and intentional.
- Run `bash scripts/ci/run_basic_ci.sh` if your change touches the current
  basic CI surface; otherwise run the narrowest relevant subset and report it.
- Do not commit, push, open a browser, or merge anything.

Final response format:

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

If no safe change is available, say so clearly under `## Feature` and explain
why under `## Risks`.
