# SEOCHO Documentation

Welcome to the central documentation index for SEOCHO. This directory tracks the active systems, decisions, and capabilities comprising our multi-agent platform.

## Active Docs

- `ARCHITECTURE.md`: system architecture and module map
- `TUTORIAL_FIRST_RUN.md`: first end-to-end run guide
- `WORKFLOW.md`: end-to-end operational workflow
- `PHILOSOPHY.md`: core design philosophy charter and operating principles
- `PHILOSOPHY_FEASIBILITY_REVIEW.md`: multi-role feasibility review framework and Go/No-Go rubric
- custom frontend backend: `evaluation/server.py` + `evaluation/static/*`
- `GRAPH_MODEL_STRATEGY.md`: graph representation model choices and rollout
- `SHACL_PRACTICAL_GUIDE.md`: practical readiness and adoption guide for SHACL-like constraints
- `ISSUE_TASK_SYSTEM.md`: sprint/roadmap issue and task operating model
- `ADD_PLAYBOOK.md`: engineering execution and landing procedure
- `CONTEXT_GRAPH_BLUEPRINT.md`: context event model and rollout
- `QUICKSTART.md`: setup and local run instructions
- `OPEN_SOURCE_PLAYBOOK.md`: extension guide for ontology/data/agent/runtime integration
- `ROADMAP.md`: planned product/engineering milestones
- `decisions/DECISION_LOG.md`: architecture decision history

## Docs Sync Integration

- `README.md` and `docs/*` changes are intended to propagate to the website via `.github/workflows/sync-docs-website.yml`.
- publish-critical sync sources:
  - `docs/README.md`
  - `docs/QUICKSTART.md`
  - `docs/ARCHITECTURE.md`
  - `docs/WORKFLOW.md`
- The planned trigger emits `repository_dispatch` to `tteon/tteon.github.io` (`event-type: seocho-docs-sync`).
- Remote rollout may remain pending until repository-owner credentials with `workflow` scope are applied.
- Local `tteon.github.io/` workspace can be used for integration testing, but remains outside `seocho` tracking.

## Archive

Archived docs are moved under `docs/archive/` when no longer part of current
operational guidance.

Current archive status:

- no archived docs tracked
