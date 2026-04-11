# Workflow

This document is the canonical workflow reference for SEOCHO operations.

## Stack Baseline

- Agent runtime: OpenAI Agents SDK
- Tracing/evaluation: Opik
- Graph backend: DozerDB
- MVP tenancy: single-tenant with `workspace_id` propagated end-to-end

## Planes

## Control Plane

Responsibilities:

- agent definitions and routing policy
- runtime authorization policy (app-level RBAC/ABAC)
- deployment, versioning, and quality gates
- decision records (ADRs) and change governance

Primary surfaces:

- `extraction/agent_server.py`
- `extraction/policy.py`
- `docs/decisions/`
- `docs/BEADS_OPERATING_MODEL.md`

## Data Plane

Responsibilities:

- data ingestion from CSV/JSON/API
- extraction, linking, deduplication
- rule inference and validation annotations
- graph load/query execution against DozerDB

Primary surfaces:

- `extraction/pipeline.py`
- `extraction/rule_constraints.py`
- `extraction/data_source.py`
- `extraction/graph_loader.py`

## End-to-End Workflow

1. Intake
- define issue scope and acceptance criteria
- assign `workspace_id`
- fill or update the relevant `docs/*` sections using the `DEV-*` prefixes defined in `docs/DEVELOPER_INPUT_CONVENTIONS.md`
- mark any blocker that should stop implementation as `DEV-INPUT-REQUIRED`
- capture work item using standardized scripts:
  - `scripts/pm/new-issue.sh`
  - `scripts/pm/new-task.sh`
- for semantic retrieval or graph-grounded answer work, align the change with
  `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`
- confirm philosophy alignment against `docs/PHILOSOPHY.md` (ontology evidence, router/graph mapping, traceability)
- for architecture-significant work, run a panel feasibility review using `docs/PHILOSOPHY_FEASIBILITY_REVIEW.md`
- before coding, have the agent restate the active `DEV-DECISION`, `DEV-CONSTRAINT`, `DEV-API-CONTRACT`, and `DEV-ACCEPTANCE` lines it will implement

2. Ingestion and graph build
- run extraction pipeline
- for interactive runtime onboarding, ingest raw text via `/platform/ingest/raw`
- parse heterogeneous sources (`text`/`csv`/`pdf`) to normalized text before extraction
- run LLM 3-pass semantic extraction (ontology candidate -> SHACL candidate -> entity graph)
- evaluate relatedness against known entities and run linking only when relatedness gate is satisfied
- select semantic artifact policy (`auto`, `draft_only`, `approved_only`) before rule application
- when governance review is required, persist draft artifacts and promote via approval lifecycle API (`/semantic/artifacts/*`)
- apply SHACL-like rule inference/validation
- run readiness check with `/rules/assess` before promoting profile to governance baseline
- save reusable rule profiles (`/rules/profiles`) in durable registry (`RULE_PROFILE_DIR/rule_profiles.db`)
- export governance artifacts (`/rules/export/cypher`, `/rules/export/shacl`)
- load graph into DozerDB

3. Agent execution
- run `/run_agent` or `/run_debate`
- for query-time entity disambiguation, run `/run_agent_semantic`
- for custom interactive UX, run `/platform/chat/send`
- monitor split health surfaces (`/health/runtime`, `/health/batch`)
- enforce runtime policy checks
- capture traces in Opik

Semantic path summary:

- semantic layer extracts entities from question
- ensure fulltext index exists (`/indexes/fulltext/ensure`) for target DBs
- fulltext search resolves graph entity candidates
- optional ontology-hint artifact generated offline via `scripts/ontology/build_ontology_hints.py`
- dedup/disambiguation reranks candidates
- router dispatches to LPG or RDF specialist agent
- answer generation agent synthesizes final response

4. Validation and landing
- run code and ops gates
- run runtime flow smoke gate (`make e2e-smoke`) when API/UI/data-plane contracts change
- run quickstart reproducibility check (raw ingest -> semantic/debate chat) before release notes
- optional one-command landing wrapper: `scripts/land.sh --task-id <id> --fix --pull --push`
- run sprint label lint (`scripts/pm/lint-items.sh --sprint <id>`)
- run agent docs lint (`scripts/pm/lint-agent-docs.sh`)
- close issue, rebase, sync, push
- verify branch is up to date with origin

Operational notes:

- use `scripts/pm/lint-items.sh` with internal `bd --no-daemon` execution to avoid local daemon startup stalls.
- current dev quality gates in `Makefile` run against `extraction-service`.
- keep graph procedure privileges scoped (`apoc.*,n10s.*`) in `docker-compose.yml`.

## Docs Website Sync

- source of truth: `README.md` + `docs/*` in this repository
- publish-critical docs for seocho.blog sync:
  - `docs/README.md`
  - `docs/QUICKSTART.md`
  - `docs/ARCHITECTURE.md`
  - `docs/WORKFLOW.md`
- website updates are currently maintained directly in the `tteon.github.io/`
  workspace and validated there with `npm run build`
- `tteon.github.io/scripts/sync.mjs` can be used as a local helper when syncing
  selected docs, but no repo-side automatic sync workflow is currently enforced

5. GitHub Automation Status

- this repository currently has no active GitHub Actions workflows
- validate locally before landing
- if package publishing is needed, run local build validation first:
  - `uv build`
  - `twine check dist/*`
- if periodic maintenance or review automation is needed later, reintroduce it
  with a new ADR rather than assuming old workflow files still exist

6. Governance loop
- log architecture decisions as ADRs
- track context graph events and quality metrics
- schedule follow-up issues for unresolved risks
