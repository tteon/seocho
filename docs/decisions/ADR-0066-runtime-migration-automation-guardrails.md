# ADR-0066: Add Runtime Migration Skill And Fast Contract Gate

Date: 2026-04-15
Status: Accepted

## Context

The staged `extraction/ -> runtime/` migration now spans multiple small slices.
Those slices repeatedly require the same contributor actions:

- read the current runtime migration contract
- preserve compatibility aliases
- normalize repo-owned tests and docs toward `runtime/*`
- update ADR and `DECISION_LOG`
- run the same focused runtime-shell validation

Without a dedicated guardrail, stale extraction-era paths can reappear in active
docs, tests, or CI coverage even when the code itself is correct.

## Decision

Add two migration-specific guardrails:

1. a repo-local Codex skill at
   `.agents/skills/runtime-migration-slice/SKILL.md`
2. a fast runtime-shell contract check at
   `scripts/ci/check-runtime-shell-contract.sh`

Wire the contract check into:

- `scripts/ci/run_basic_ci.sh`
- `.githooks/pre-commit` for relevant staged runtime-migration files

## Consequences

### Positive

- runtime migration slices follow a more repeatable workflow
- contributor docs, tests, and aliases are less likely to drift
- long-horizon Codex work gets a narrower, repo-specific execution playbook

### Negative

- pre-commit hook behavior becomes slightly more complex
- the contract check must be maintained as the migration surface changes

## Out Of Scope

- broad automation for all repository work
- removal of existing compatibility aliases
- user-facing API changes
