# ADR-0086: User-First README And Docs Entry Points

Date: 2026-04-18
Status: Accepted

## Context

SEOCHO's GitHub README and docs index had accumulated too much internal
architecture and contributor process detail above the fold.

That caused three product problems:

- first-run setup paths were visible, but not dominant
- copy-paste feature snippets were buried under package and migration detail
- architecture depth existed, but there was no clear public deep-dive CTA for
  users who wanted it

The result was a repo landing page that behaved more like an internal handbook
than a product entry point.

## Decision

Make README and docs home explicitly user-first.

- `README.md` should lead with:
  - one-line product value
  - start-path table
  - quickstart snippet
  - a small set of copy-paste feature snippets
  - a clear architecture deep-dive CTA
- direct framework comparison should not lead the page
- `docs/README.md` should behave as a public docs index first, not as a mixed
  contributor/operator dump
- the mirrored website docs home should track the same source-of-truth
  structure from `docs/README.md`

## Consequences

- GitHub landing becomes more useful for first-time users and evaluators
- architecture and internal workflow material still exists, but moves below the
  first-run path
- `seocho.blog` docs home and GitHub docs entrypoints stay aligned
- contributor references remain available from docs, but stop competing with
  onboarding content above the fold

## Follow-Ups

- If a dedicated DeepWiki or comparable architecture microsite is introduced,
  swap the current architecture CTA target without changing the README/docs
  structure.
- Keep feature snippets synchronized with the actual stable public SDK surface.

## Related Documents

- `README.md`
- `docs/README.md`
- `tteon.github.io/src/content/docs/docs/index.md`
