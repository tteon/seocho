# ADR-0113: Version-control shared Claude Code skills under `.claude/skills/`

Date: 2026-06-13

Status: Accepted

## Context

`.claude/` has been an intentionally untracked surface (Public Repo Hygiene
in `CLAUDE.md`, `.gitignore`, and the root-hierarchy contract
`scripts/ci/check-root-hierarchy-contract.sh`). That blanket exclusion was
written for **editor/agent local state** — `settings.local.json`, caches,
transient session data — which must not leak into a public middleware repo.

Claude Code **skills** (`.claude/skills/<name>/SKILL.md`) are a different
kind of artifact: reusable, reviewable team tooling that encodes how to drive
the product (e.g. `seocho-e2e` authors and runs `seocho run` / `seocho sweep`
flows). Today they live only in each developer's user-global
`~/.claude/skills/`, so they are invisible to review, drift per-machine, and
do not travel with the codebase. Keeping product-operation knowledge out of
the repo contradicts the "SEOCHO is middleware, public code should help users
build with it" framing.

Claude Code auto-loads project skills from a repo's `.claude/skills/` on
clone. So the only mechanism that makes a skill both **tracked** and
**zero-install** is to track that one subdirectory.

## Decision

Track **`.claude/skills/`** in the repo; keep the rest of `.claude/`
untracked.

- `.gitignore`: replace `.claude/` with `.claude/*` + `!.claude/skills/`.
- `scripts/ci/check-root-hierarchy-contract.sh`: remove `.claude` from the
  generic forbidden list and add a dedicated check that flags any tracked
  file under `.claude/` **except** `.claude/skills/`.
- `CLAUDE.md` Public Repo Hygiene: document the `.claude/skills/` exception.

Skills live at `.claude/skills/<name>/SKILL.md` (+ optional `scripts/`),
matching the user-global layout, so a skill can be moved between the two
without edits.

## Consequences

- Skills are reviewed in PRs, version with the code, and activate on clone
  with no install step.
- Local agent state (`settings.local.json`, etc.) stays untracked — the
  original hygiene intent is preserved; only the skills subtree is exempted.
- The contract still fails the build if anything **other than** skills is
  committed under `.claude/`, so the exception cannot silently widen.
- First skill landed under this policy: `seocho-e2e` (author + run SEOCHO
  YAML e2e flows).
