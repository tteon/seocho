# ADR-0196: Untrack every agent-state directory, including `.claude/skills/`

- Status: Accepted
- Date: 2026-08-16
- **Supersedes `ADR-0113`** (track Claude skills in the repo)
- Related: `ADR-0195` (root compose entry point)

## Context

`CLAUDE.md` lists six directories as intentionally non-tracked public surfaces:
`.agents/`, `.beads/`, `.claude/`, `.githooks/`, `.jules/`, `.serena/`. `ADR-0113`
carved one exception out of that: `.claude/skills/`, on the reasoning that a
shared project skill is version-controlled tooling and auto-loads on clone.

Two things have since become clear.

**The exception cost more than it bought.** It was one file —
`.claude/skills/seocho-e2e/SKILL.md` — and it required a bespoke branch in the
root-hierarchy contract (`git ls-files -- ".claude" ":(exclude).claude/skills"`)
that had to be maintained separately from the plain forbidden-path list.

**A partial rule is the kind that drifts.** The same contract was missing
`.jules/` and `.serena/` entirely, so `.jules/bolt.md` sat in the tree while the
check reported passing — and `.gitignore` already listed the directory, which is
precisely how it got there: tracked before the ignore rule existed, and ignore
does nothing for a file git already follows. A hygiene list with one carve-out
and two omissions certifies the drift it is meant to catch.

Neither reference project keeps agent state in the tree. vLLM and LangChain both
have `.claude/` present locally and absent from the repository.

## Decision

`.claude/` is forbidden outright, alongside `.serena/`, and the special-case
branch is deleted. The forbidden list now matches `CLAUDE.md` exactly, with no
exceptions to remember.

## Consequences

- `seocho-e2e` no longer arrives on clone. That is the real cost of this
  decision, and it is accepted: a skill that only one tool reads is not a
  repository artefact, and if it needs to be shared it belongs in `docs/` or
  `scripts/` where every contributor can find it regardless of editor.
- The contract is a single list, verified by planting a forbidden path and
  confirming the check fails rather than assuming it does.
- `CLAUDE.md`'s public-repo-hygiene section loses its exception clause.
