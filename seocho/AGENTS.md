# SEOCHO Rig Agent Rules

## Scope

These instructions apply to work executed under `seocho/`.

## Core Rules

- Keep changes small and traceable to one issue.
- Prefer documented commands over ad-hoc shell sequences.
- Never commit credentials or machine-local secrets.
- Treat runtime files as ephemeral; do not rely on them for source history.

## Required Session Loop

1. Confirm context: `bd ready`, `bd show <id>`.
2. Claim work: `bd update <id> --status in_progress`.
3. Implement and verify with focused checks.
4. Sync issue and git state: `bd sync`.
5. Land cleanly: rebase, push, and verify branch state.

## Escalation Conditions

Open a follow-up issue immediately when:

- root cause is unknown,
- fix is partial,
- behavior is flaky/non-deterministic,
- or the required change crosses service boundaries.

## Handoff Minimum

Every handoff must include:

- what changed,
- what is still open,
- exact file paths touched,
- latest check command and result.
