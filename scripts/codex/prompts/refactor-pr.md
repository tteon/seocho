Use the repo-local skill `$refactor-pr`.

Open one small draft PR in the `refactor` lane.

Constraints:
- reduce duplication or improve maintainability
- no intended behavior change
- no broad renaming campaign
- no unrelated cleanup
- keep the PR small and reviewable
- run focused validation before finishing
- do not merge or push directly to `main`

Return a PR-ready summary with exactly these headings:

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
