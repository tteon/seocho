Use the repo-local skill `$periodic-review-pr`.

Task:

- Inspect the current SEOCHO repository and prepare one reviewable improvement
  that is suitable for a draft pull request.
- Respect `AGENTS.md`, `README.md`, `docs/WORKFLOW.md`, and
  `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md` when relevant.

Scope constraints:

- Choose exactly one cohesive improvement.
- Allowed themes: focused refactor, small developer-facing feature,
  targeted regression tests, release/packaging hardening, CI/docs cleanup tied
  to implemented behavior.
- Do not attempt a broad feature, multi-area refactor, dependency sweep, or
  speculative product change.
- Do not touch generated artifacts or unrelated dirty files.
- If the best idea is larger than a small PR, make no code changes.

Execution rules:

- Read the current repo state first.
- Keep edits minimal and intentional.
- Run `bash scripts/ci/run_basic_ci.sh` when the touched behavior falls inside
  the repository basic CI surface; otherwise run the narrowest relevant subset
  and report it.
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
