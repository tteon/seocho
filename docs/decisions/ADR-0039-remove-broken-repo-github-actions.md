# ADR-0039: Remove Broken Repository GitHub Actions

Date: 2026-04-11
Status: Accepted

## Context

The repository accumulated multiple GitHub Actions workflows for scheduled
Codex review, comment-triggered merge, package publishing, and integration
checks. In practice, the automation surface had drifted out of working order
and was no longer a trustworthy operational path.

Keeping broken workflows in the repository caused two problems:

- maintainers and agents could no longer tell which automation was real versus
  stale
- README and workflow docs implied CI and automation guarantees that no longer
  held

## Decision

SEOCHO will remove all repository-local GitHub Actions workflows for now.

This means:

1. delete `.github/workflows/*` from the repository
2. update repo instructions and docs to state that GitHub Actions automation is
   currently disabled
3. require local validation as the active delivery path
4. reintroduce any future GitHub automation only through a fresh ADR and a
   working validation pass

## Consequences

Positive:

- repository instructions become honest again
- maintainers stop paying the cost of failing or misleading CI
- future automation can be reintroduced from a clean baseline

Tradeoffs:

- there is no repo-side CI gate right now
- package publishing and comment-triggered merge become manual again
- historical ADRs describing prior automation remain in the repo as history,
  but are superseded operationally by this decision

## Implementation Notes

- removed workflow files from `.github/workflows/`
- updated `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/README.md`, and
  `docs/WORKFLOW.md`
