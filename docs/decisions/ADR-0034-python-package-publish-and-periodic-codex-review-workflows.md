# ADR-0034: Python Package Publish And Periodic Codex Review Workflows

Date: 2026-04-09
Status: Accepted

## Context

SEOCHO now has a publishable Python SDK surface, but the repository still
lacked two important operational workflows:

- a release-path GitHub workflow that builds, validates, and publishes the
  package to TestPyPI or PyPI
- a broader scheduled Codex review workflow that goes beyond tiny maintenance
  while still staying within a safe draft-PR envelope

The existing daily Codex workflow was intentionally narrow. That is useful for
small maintenance, but it is too constrained for periodic developer-experience
improvements, refactors, or packaging hardening.

## Decision

SEOCHO will add two new automation contracts:

1. a Python package publish workflow:
   - build distributions with `python -m build`
   - verify metadata with `python -m twine check dist/*`
   - publish through PyPI trusted publishing
   - support manual TestPyPI smoke publishes and production PyPI publishes
2. a periodic Codex review workflow:
   - inspect the repository on a weekly cadence
   - choose exactly one bounded improvement
   - allow small developer-facing improvements or refactors in addition to
     maintenance
   - open or update a draft PR only

## Consequences

Positive:

- the Python SDK has a clear automated release path
- maintainers can dry-run package release to TestPyPI before production publish
- Codex automation can now cover a wider class of low-risk reviewable
  improvements without overloading the daily maintenance lane

Tradeoffs:

- PyPI trusted publishing requires one-time registry and GitHub environment
  setup before the workflow is usable
- the periodic review workflow is intentionally still bounded, so larger
  feature ideas remain manual product work
- running both daily and periodic Codex workflows adds PR automation surface
  that maintainers must monitor

## Implementation Notes

- publish workflow lives in `.github/workflows/publish-python-package.yml`
- periodic review workflow lives in
  `.github/workflows/periodic-codex-review.yml`
- periodic review prompt lives in
  `.github/codex/prompts/periodic-review-pr.md`
- periodic review skill lives in
  `.agents/skills/periodic-review-pr/SKILL.md`
