Use the repo-local skill `$e2e-investigation-pr`.

Open one small draft PR in the `e2e-investigation` lane.

Constraints:
- reproduce exactly one concrete runtime or smoke failure
- add the smallest regression coverage that proves it
- implement the smallest viable fix
- do not bundle unrelated refactors
- run the narrowest validation that proves the fix
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
