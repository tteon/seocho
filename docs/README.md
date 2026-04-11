# SEOCHO Documentation

Welcome to the central documentation index for SEOCHO. This directory tracks the active systems, decisions, and capabilities comprising our multi-agent platform.

## Canonical Start Docs

Read these first unless you already know exactly which area you need:

- `QUICKSTART.md`: shortest local success path
- `PYTHON_INTERFACE_QUICKSTART.md`: public Python SDK and pip-install path
- `APPLY_YOUR_DATA.md`: how to ingest your own records and query them safely
- `WORKFLOW.md`: canonical operational workflow
- `PRD_MVP.md`: current product scope and MVP contract
- `GRAPH_MEMORY_API.md`: target public memory-first API
- `GRAPH_RAG_AGENT_HANDOFF_SPEC.md`: intent-first graph answer contract
- `AGENT_DEVELOPMENT.md`: coding-agent execution guide
- `CODING_STYLE.md`: implementation and test conventions
- `DEVELOPER_INPUT_CONVENTIONS.md`: required `DEV-*` prefixes for document-first development
- `ISSUE_TASK_SYSTEM.md`: sprint and task governance
- `BEADS_OPERATING_MODEL.md`: `.beads` execution contract
- `decisions/DECISION_LOG.md`: architecture decision history

## Onboarding Order

Use this path if you are new to the repository:

1. `QUICKSTART.md`
2. `PYTHON_INTERFACE_QUICKSTART.md`
3. `APPLY_YOUR_DATA.md`
4. `TUTORIAL_FIRST_RUN.md`
5. `BEGINNER_PIPELINES_DEMO.md`
6. `OPEN_SOURCE_PLAYBOOK.md`

Role split:

- `QUICKSTART.md` is the shortest successful run
- `PYTHON_INTERFACE_QUICKSTART.md` is the public Python SDK quickstart
- `APPLY_YOUR_DATA.md` is the bring-your-own-data ingestion and query guide
- `TUTORIAL_FIRST_RUN.md` is the manual API verification path
- `BEGINNER_PIPELINES_DEMO.md` is the scripted demo path
- `OPEN_SOURCE_PLAYBOOK.md` is the contributor path

Current developer-facing execution order:

1. semantic layer first
2. bounded repair when slot fill is insufficient
3. explicit advanced debate only when cross-graph comparison is required

## Specialized Reference Docs

Use these when changing a specific subsystem or workflow:

- `ARCHITECTURE.md`: system architecture and module map
- `GRAPH_RAG_AGENT_HANDOFF_SPEC.md`: intent-first Graph-RAG design brief
- `AGENT_SERVER_REFACTOR_PLAN.md`: staged decomposition plan for `extraction/agent_server.py`
- `GRAPH_MODEL_STRATEGY.md`: ontology and graph representation choices
- `SHACL_PRACTICAL_GUIDE.md`: SHACL-like adoption and readiness flow
- `PHILOSOPHY.md`: design philosophy charter
- `PHILOSOPHY_FEASIBILITY_REVIEW.md`: multi-role Go/No-Go review framework
- `CONTEXT_GRAPH_BLUEPRINT.md`: context event model and rollout
- `RUNTIME_FILE_ISOLATION.md`: tracked vs untracked runtime file policy
- `EMBEDDED_GIT_CLONE_POLICY.md`: embedded clone policy
- `schemas/context-event.schema.json`: canonical `cg.v0` event contract
- `BEGINNER_PIPELINES_DEMO.md`: staged demo path
- `TUTORIAL_FIRST_RUN.md`: extended first-run API walkthrough
- `OPEN_SOURCE_PLAYBOOK.md`: open-source onboarding and contribution path
- `ROADMAP.md`: planned product and engineering milestones

## Root-Level Contributor Docs

- `../CONTRIBUTING.md`: contribution flow, coding standards, and PR requirements
- `../LICENSE`: repository license (MIT)
- `../SECURITY.md`: vulnerability reporting process

## Codex Automation Surfaces

- `../.agents/skills/daily-maintenance-pr/SKILL.md`: repo-local skill for
  small scheduled maintenance PRs
- `../.agents/skills/periodic-review-pr/SKILL.md`: repo-local skill for
  scheduled repository review and refactor PRs
- `../.github/codex/prompts/daily-maintenance-pr.md`: Codex prompt used by the
  scheduled maintenance workflow
- `../.github/codex/prompts/periodic-review-pr.md`: Codex prompt used by the
  periodic review workflow
- `../.github/workflows/daily-codex-maintenance.yml`: daily GitHub App PR
  automation
- `../.github/workflows/periodic-codex-review.yml`: periodic GitHub App review
  PR automation
- `../.github/workflows/publish-python-package.yml`: Python package build and
  publish workflow for TestPyPI/PyPI
- `../.github/workflows/pr-comment-merge.yml`: maintainer-triggered `/go`
  squash merge workflow

## Docs Sync Integration

- `README.md` and selected `docs/*` pages are the source material for website docs.
- publish-critical sync sources:
  - `docs/README.md`
  - `docs/QUICKSTART.md`
  - `docs/APPLY_YOUR_DATA.md`
  - `docs/PYTHON_INTERFACE_QUICKSTART.md`
  - `docs/ARCHITECTURE.md`
  - `docs/WORKFLOW.md`
- Website updates are currently maintained directly in the local `tteon.github.io/`
  workspace and validated there with `npm run build`.
- `tteon.github.io/scripts/sync.mjs` may be used as a local helper, but no repo-side
  automatic sync workflow is currently enforced.

## Archive

Archived docs are moved under `docs/archive/` when no longer part of current
operational guidance.

Current archive status:

- `archive/ADD_PLAYBOOK.md`: replaced by `WORKFLOW.md` + `BEADS_OPERATING_MODEL.md`
- `archive/AGENT_DEVELOPMENT_AUDIT.md`: historical audit snapshot only
- `archive/README.md`: archive rationale and index
