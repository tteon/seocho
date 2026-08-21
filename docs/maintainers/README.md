# Maintainer Docs

This directory stores maintainer-facing docs that are still active but should
not be part of the first user reading path.

Keep user onboarding in the root README, `QUICKSTART.md`, and the public docs
index. Keep implementation guardrails, internal conventions, and maintainer
workflow notes here when they are not required by new users.

| Doc | Role |
|---|---|
| `AGENT_DEVELOPMENT.md` | How to build and wire agents inside the runtime |
| `AGENT_EXECPLAN_CONVENTIONS.md` | Supplement to the root `AGENTS.md`: when a task needs an ExecPlan and the local decision style |
| `EXECPLAN_SPEC.md` | What an ExecPlan must contain; plans themselves live in `docs/execplans/` |
