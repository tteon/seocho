# ADR-0008: Agent Documentation Lint Automation

- Status: Accepted
- Date: 2026-02-15
- Deciders: SEOCHO team

## Context

`CLAUDE.md` and `AGENTS.md` became primary execution contracts, but there was
no automated check to detect baseline drift (stack, workflow, links).

## Decision

Add `scripts/pm/lint-agent-docs.sh` to enforce:

- required agent-facing files exist
- baseline stack markers exist in docs
- key workflow commands and links remain present

The lint is intended as a fast pre-landing quality gate.

## Consequences

Positive:

- prevents accidental removal of critical operating guidance
- keeps onboarding consistency for coding agents

Trade-offs:

- static string checks may need updates when docs intentionally evolve
