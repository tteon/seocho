# Contributing to SEOCHO

Thank you for contributing to SEOCHO. This guide defines the minimum process for safe and reviewable changes.

## 1. Read Before Coding

Read these docs in order:

1. `README.md`
2. `CLAUDE.md`
3. `docs/WORKFLOW.md`
4. `docs/ISSUE_TASK_SYSTEM.md`
5. `docs/decisions/DECISION_LOG.md`

Current baseline must remain aligned:

- OpenAI Agents SDK
- Opik tracing/evaluation
- DozerDB backend
- single-tenant MVP with `workspace_id` propagation
- Owlready2 only in offline ontology governance path

## 2. Work Intake

For core maintainers using `bd`, follow:

```bash
bd ready
bd show <id>
bd update <id> --status in_progress
```

For new work items, use standard scripts:

```bash
scripts/pm/new-issue.sh ...
scripts/pm/new-task.sh ...
```

Active items (`open`, `in_progress`, `blocked`) must include:

- `sev-*`, `impact-*`, `urgency-*`, `sprint-*`, `roadmap-*`, `area-*`, `kind-*`

## 3. Development and PR Flow

1. Fork and clone your repository copy.
2. Create a focused branch (`feat/...`, `fix/...`, `docs/...`).
3. Keep changes scoped and testable.
4. Preserve runtime guardrails:
   - propagate `workspace_id` in runtime-facing changes
   - enforce policy checks for new endpoints/actions
   - keep heavy ontology reasoning out of request hot path
5. Add or update tests for changed behavior.
6. Run focused quality gates before PR:
   - relevant `pytest` suites
   - `make e2e-smoke` when API/UI/runtime contracts change
   - `scripts/pm/lint-agent-docs.sh` for docs/rules baseline
7. Open a PR against `main` with:
   - summary of behavior changes
   - test evidence (commands + results)
   - explicit note for any test gaps not run

## 4. Coding Standards

- use type hints on function signatures
- use centralized config (`extraction/config.py`)
- use logging, not `print`
- avoid broad or hidden side effects
- do not commit secrets or credentials

Commit prefix conventions:

- `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`

## 5. Documentation and ADR Rules

For architecture or workflow changes:

- update `README.md` and relevant docs in `docs/*`
- add/update ADR in `docs/decisions/ADR-*.md`
- append decision summary in `docs/decisions/DECISION_LOG.md`

## 6. License and Compliance

- Repository license: MIT (`LICENSE`)
- Inbound = outbound: contributions are licensed under MIT
- Only add third-party code/dependencies with compatible licenses
- When adding new dependencies, include package name/version/license in PR description

## 7. Security Reporting

For security vulnerabilities, follow `SECURITY.md` instead of opening a public issue first.
