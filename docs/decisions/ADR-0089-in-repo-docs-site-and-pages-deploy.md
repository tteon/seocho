# ADR-0089: In-Repo Docs Site and Pages Deploy

Date: 2026-05-03
Status: Accepted

## Context

`seocho.blog` has been running from a separate `tteon.github.io` repository
that mirrored selected docs from the main `seocho` repository.

That split created three recurring problems:

- the website depended on string-rewrite sync scripts with duplicated route
  transformation logic
- generated site content and source docs could drift independently across two
  repositories
- scheduled auto-sync and PR-opening policy failures could break the website
  maintenance path even when the source docs themselves were correct

The project still wants a dedicated Astro/Starlight presentation layer, but it
does not need a second repository to achieve that.

## Decision

SEOCHO will move the docs site into the main repository and adopt a single-repo
ownership model:

1. the tracked Astro/Starlight app for `seocho.blog` lives in `website/`
2. canonical source docs remain repo-root `README.md` plus `docs/*`
3. selected `/docs/*` and `/blog/*` site pages are generated at build/dev time
   by `website/scripts/generate-docs.mjs`
4. generated mirror files under `website/src/content/docs/docs/` are derived
   artifacts and must not be edited directly
5. site quality checks live in `.github/workflows/docs-site-quality.yml`
6. GitHub Pages deploy lives in `.github/workflows/docs-site-deploy.yml`
7. the old separate-repository sync contract is no longer the active operating
   model

## Consequences

Positive:

- one repository owns both the docs source and the site presentation layer
- doc changes and site validation now run in the same PR/CI surface
- cross-repo sync drift and PR-policy failures are removed from the critical
  path
- the site generator has one canonical implementation instead of duplicated
  sync and drift-check scripts

Tradeoffs:

- the main repository now carries a Node-based website subproject
- build/dev for the website requires a generation step before Astro runs
- generated site pages remain a derived layer, so route-rewrite logic still
  exists, but it is now local to one repository

## Implementation Notes

- site app root: `website/`
- generator: `website/scripts/generate-docs.mjs`
- site quality workflow: `.github/workflows/docs-site-quality.yml`
- site deploy workflow: `.github/workflows/docs-site-deploy.yml`
- repo-doc contract: `scripts/ci/check-doc-contracts.sh`
