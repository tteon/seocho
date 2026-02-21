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
- `POST /rules/assess`
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

## 12. Commit Conventions & Website Sync

### Commit Conventions (Semantic Versioning)
We enforce STRICT Semantic Versioning style commit prefixes to keep our history clean and to fuel the automated `Updates` section of the website changelog.
- `feat:` — New features or significant additions (e.g., `feat: Add Parallel Debate orchestrator`)
- `fix:` — Bug fixes (e.g., `fix: Resolve FAISS index out of bounds error`)
- `docs:` — Documentation and website changes (e.g., `docs: Update CLAUDE.md for agent collaboration`)
- `refactor:` — Code restructuring without logic changes
- `chore:` — Tooling, dependency, or minor configuration updates
- `test:` — Adding or updating test suites

### Automated Website Syncing
The `seocho` primary repository acts as the source of truth for all documentation.
Website sync is designed to run through `.github/workflows/sync-docs-website.yml` with `repository_dispatch` to `tteon.github.io`, but rollout may require repository owner credentials (`workflow`-scoped token).
If the workflow is not yet active in the remote repository, treat docs-sync as pending owner implementation.

## 13. Reliability Notes (2026-02-20)

- `Makefile` quality gates must target `extraction-service` (not `engine`).
- Neo4j/DozerDB procedure privileges must stay scoped to `apoc.*,n10s.*` (no wildcard unrestricted).
- API/middleware tests should prefer `httpx.ASGITransport` + `AsyncClient` over `TestClient` in this repo environment.
- When local `bd` daemon startup is unstable, run lint via non-daemon mode (`bd --no-daemon`) to avoid hanging quality gates.
- `tteon.github.io/` can exist as a local nested workspace for website validation, but it should remain untracked by the parent `seocho` repository.
- Pushing workflow-file changes requires a PAT with `workflow` scope (or equivalent owner permissions).

## 14. Philosophy Alignment

All significant implementation changes should align with `docs/PHILOSOPHY.md`.

Critical alignment checks:

- heterogeneous-source extraction should produce ontology-governed semantics (rules + entity links), not plain unstructured outputs only.
- graph instance lifecycle and graph-agent lifecycle should remain 1:1 unless an ADR explicitly changes this.
- router/supervisor request allocation should be grounded in ontology-backed graph metadata.
- backend trace topology metadata is a contract for frontend DAG rendering, not an optional hint.
- Opik traces should preserve enough metadata to audit routing, semantic disambiguation, and synthesis paths.

## 15. Architecture Priority Execution (Active)

Execution order (highest first):

1. runtime contract stability (SDK adapter + contract tests)
2. real-database-only agent provisioning and degraded-state reporting
3. graph query durability migration (`id` -> `elementId`)
4. runtime vs batch process/health isolation
5. agent readiness state machine for routing/supervision
6. `/rules/assess` governance automation in promotion flows

## 16. User-First Release Gate

Any user-facing change must preserve a reproducible quickstart path:

1. ingest raw records (`/platform/ingest/raw`)
2. ensure fulltext (`/indexes/fulltext/ensure`)
3. run semantic and debate chat (`/api/chat/send`)
4. verify strict integration smoke (`make e2e-smoke`)

If this path is broken, do not treat the release as complete.

## 17. Documentation Sync Contract

For seocho.blog sync, keep these docs current as first-class release artifacts:

- `docs/README.md`
- `docs/QUICKSTART.md`
- `docs/ARCHITECTURE.md`
- `docs/WORKFLOW.md`

Docs updates that change user behavior or architecture intent must include a decision log update (and ADR when non-trivial).
