# ADR-0036: Documentation Consistency CI Contract

Date: 2026-04-11
Status: Accepted

## Context

SEOCHO now has two documentation surfaces:

- repository docs in `README.md` and `docs/*`
- website docs in `tteon.github.io`

Recent doc drift showed two concrete failure modes:

- stale runtime examples continued to reference the old
  `http://localhost:8501/api/chat/send` endpoint instead of
  `http://localhost:8001/platform/chat/send`
- website copy still implied automatic repository-to-website sync even though
  docs are currently maintained directly in the website workspace

These errors are small, but they break copy-paste onboarding and create false
operator expectations.

## Decision

SEOCHO will enforce documentation consistency in CI with a split contract:

1. the main repository will run repo-doc contract checks in
   `.github/workflows/docs-consistency.yml`
2. the repository check script will reject stale endpoint and stale sync wording
   in active developer-facing docs
3. the website repository will run its own docs quality workflow in
   `tteon.github.io/.github/workflows/docs-quality.yml`
4. website CI will reject stale endpoint examples and stale "synced
   automatically" wording, then run `npm run build`
5. `tteon.github.io/scripts/sync.mjs` remains a local helper only; it must not
   imply that remote automatic sync is already enforced

## Consequences

Positive:

- copy-paste onboarding examples fail earlier in CI rather than after publish
- repo docs and website docs share a narrower, explicit contract
- the current manual website maintenance model is represented honestly

Tradeoffs:

- two repositories now carry separate documentation workflows
- doc-only wording drift can fail CI even when product code is unchanged
- future reintroduction of automatic sync will require updating both CI checks
  and the documented contract

## Implementation Notes

- repo check script lives in `scripts/ci/check-doc-contracts.sh`
- repo workflow lives in `.github/workflows/docs-consistency.yml`
- website check script lives in `tteon.github.io/scripts/check-doc-quality.sh`
- website workflow lives in `tteon.github.io/.github/workflows/docs-quality.yml`
