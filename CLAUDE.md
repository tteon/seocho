# CLAUDE.md

Agent execution guide for this repository.
Use this file as the primary operational contract when implementing changes.

## 1. Current Product Consensus

- Agent runtime: **OpenAI Agents SDK**
- Trace/evaluation: **Opik**
- Graph DB backend: **DozerDB** (fixed)
- Tenancy mode: **single-tenant MVP** with `workspace_id` propagated end-to-end
- Ontology governance: **Owlready2 in offline path only** (no heavy reasoning in request hot path)

## 2. Source Of Truth Docs

Read in this order before significant changes:

1. `README.md`
2. `docs/WORKFLOW.md`
3. `docs/GRAPH_MODEL_STRATEGY.md`
4. `docs/ISSUE_TASK_SYSTEM.md`
5. `docs/decisions/DECISION_LOG.md`

When architecture changes, add/update ADRs under `docs/decisions/`.

## 3. Control Plane vs Data Plane

## Control Plane

Responsibilities:

- agent routing/instructions
- runtime policy/authorization
- quality gates and release/landing workflow
- architecture decisions and governance

Primary surfaces:

- `extraction/agent_server.py`
- `extraction/policy.py`
- `docs/decisions/*`
- `docs/ADD_PLAYBOOK.md`

## Data Plane

Responsibilities:

- document ingestion and extraction
- entity linking and deduplication
- rule inference/validation lifecycle
- graph storage/query on DozerDB

Primary surfaces:

- `extraction/pipeline.py`
- `extraction/rule_constraints.py`
- `extraction/rule_api.py`
- `extraction/rule_profile_store.py`
- `extraction/rule_export.py`

## 4. Mandatory Workflow For Agents

## 4.1 Start

1. inspect active work: `bd ready`
2. open issue: `bd show <id>`
3. claim: `bd update <id> --status in_progress`
4. if creating new work, use standardized scripts:
   - `scripts/pm/new-issue.sh`
   - `scripts/pm/new-task.sh`

## 4.2 During Implementation

- keep scope tight to one issue/feature slice
- preserve `workspace_id` in new runtime-facing contracts
- add/adjust tests for modified behavior
- update docs for user-visible changes

## 4.3 Before Landing

1. run focused tests
2. run sprint lint when applicable:
   - `scripts/pm/lint-items.sh --sprint <id>`
3. close or handoff issue
4. `git pull --rebase`
5. `bd sync` (best effort; known workspace issue may fail)
6. `git push`
7. `git status` must show up-to-date with `origin/main`

Push target is always `main`.

## 5. Issue/Task Governance

For active work items (`open`, `in_progress`, `blocked`), collaboration labels are required:

- `sev-*`
- `impact-*`
- `urgency-*`
- `sprint-*`
- `roadmap-*`
- `area-*`
- `kind-*`

Sprint commands:

```bash
scripts/pm/sprint-board.sh --sprint 2026-S03
scripts/pm/lint-items.sh --sprint 2026-S03
```

See `docs/ISSUE_TASK_SYSTEM.md` for full policy.

## 6. Runtime/API Guardrails

## 6.1 Workspace-Aware Contracts

- runtime write/compute APIs must include `workspace_id`
- validate format with policy checks

## 6.2 Rules API Surface

Current endpoints:

- `POST /rules/infer`
- `POST /rules/validate`
- `POST /rules/profiles`
- `GET /rules/profiles`
- `GET /rules/profiles/{profile_id}`
- `POST /rules/export/cypher`

Rules constraints:

- `required` maps to Cypher constraints
- unsupported mappings (`datatype`, `enum`, `range`) must be explicit in response

## 6.3 Owlready2 Boundary

- allowed: offline ontology validation/compilation flows
- forbidden: synchronous heavy ontology reasoning in request hot path

## 7. Coding Standards

- use type hints on function signatures
- prefer deterministic, testable behavior
- no hardcoded credentials
- centralized config only (`extraction/config.py`)
- logging over print
- no destructive git commands

## 8. DozerDB/Graph Safety Rules

- database names must pass registry validation
- dynamic labels/properties must be validated before Cypher interpolation
- query tools should remain read-safe unless write mode is explicitly required

## 9. Observability Requirements

- use `@track` for critical orchestration functions
- include `workspace_id` and user context in trace metadata where applicable
- use Opik for runtime tracing; avoid ad-hoc trace-only side channels

## 10. Frontend-Driven Upload Flow (Target)

Product expectation for upload flow:

1. user uploads document(s)
2. structure/chunk extraction
3. ontology candidate + graph extraction
4. SHACL-like rule inference/validation
5. profile save and export plan
6. graph persisted/queryable in DozerDB

Reference: `docs/GRAPH_MODEL_STRATEGY.md`

## 11. Definition Of Done

- code changes implemented
- tests for changed behavior pass
- docs updated (README/docs/ADR as needed)
- issue/task state updated
- changes pushed to `origin/main`
