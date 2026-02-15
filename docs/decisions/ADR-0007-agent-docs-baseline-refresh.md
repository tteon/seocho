# ADR-0007: Agent Documentation Baseline Refresh

- Status: Accepted
- Date: 2026-02-15
- Deciders: SEOCHO team

## Context

Agent-facing docs contained stale stack assumptions and incomplete execution
guidance for the current operating model.

## Decision

Refresh root agent documents:

- `CLAUDE.md` becomes the primary execution contract
- `AGENTS.md` becomes concise operational rules

Both documents now align on:

- stack baseline (OpenAI Agents SDK, Opik, DozerDB)
- `workspace_id` and runtime guardrails
- issue/task governance and PM scripts
- landing workflow and documentation obligations

## Consequences

Positive:

- faster and safer onboarding for coding agents
- reduced ambiguity in workflow and delivery expectations

Trade-offs:

- requires ongoing maintenance to keep docs synchronized with implementation
