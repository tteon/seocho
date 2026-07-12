# Workflow

This document is the canonical workflow reference for SEOCHO operations.

## Stack Baseline

- Agent runtime: OpenAI Agents SDK
- Tracing/evaluation contract: vendor-neutral (`none|console|jsonl|opik`)
- Preferred team observability backend: Opik
- Canonical neutral trace artifact: JSONL
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

- `runtime/agent_server.py`
- `runtime/memory_service.py`
- `runtime/middleware.py`
- `runtime/policy.py`
- `docs/decisions/`
- `docs/ISSUE_TASK_SYSTEM.md`

Long-term target:

- `runtime/` becomes the canonical deployment-shell package
- `extraction/` shrinks to extraction-only concerns or compatibility wrappers
- runtime-package slices are landing incrementally: `agent_server`,
  `agent_readiness`, `middleware`, `memory_service`, `server_runtime`,
  `public_memory_api`, `runtime_ingest`, and `policy` now live under
  `runtime/` with flat `extraction/*` compatibility aliases

## Data Plane

Responsibilities:

- data ingestion from CSV/JSON/API
- extraction, linking, deduplication
- rule inference and validation annotations
- graph load/query execution against DozerDB

Primary surfaces:

- `src/seocho/rules.py` — canonical rule inference/validation
- `src/seocho/index/pipeline.py` — canonical indexing with `enable_rule_constraints` + `embedding_backend`
- `src/seocho/index/linker.py` — canonical embedding-based entity linker
- `extraction/pipeline.py` — legacy batch pipeline
- `extraction/rule_constraints.py` — re-export shim to `seocho.rules`
- `extraction/data_source.py`
- `extraction/graph_loader.py`

## End-to-End Workflow

1. Intake
- define issue scope and acceptance criteria
- assign `workspace_id`
- fill or update the relevant `docs/*` sections using the `DEV-*` prefixes defined in `docs/DEVELOPER_INPUT_CONVENTIONS.md`
- mark any blocker that should stop implementation as `DEV-INPUT-REQUIRED`
- capture public work state in GitHub issues, pull requests, or the current
  maintainer-designated tracker
- keep local agent coordination tools private to the developer workspace; do
  not commit `.beads/`, `.agents/`, `.claude/`, or similar local tool state
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
- capture traces through the configured observability backend
- prefer `jsonl` as the portable artifact and Opik as the optional team exporter

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
- when performance work is in scope, run the relevant benchmark track before and
  after the change:
  - `private finance corpus` for ingestion / finance-domain QA
  - `GraphRAG-Bench` for retrieval / reasoning
  - the bundled tutorial sample is onboarding-only and must not be reported as
    benchmark evidence
- run agent docs lint (`scripts/pm/lint-agent-docs.sh`)
- close or update the linked public issue or PR
- rebase, push
- verify branch is up to date with origin
- for releases, follow `docs/RELEASE_AND_COMMUNITY_OPERATIONS.md`, update
  `CHANGELOG.md`, and draft the `#seocho` Discord announcement before publishing

Operational notes:

- local tracker linting may be used in maintainer workspaces, but it is not a
  public repository contract
- current dev quality gates in `Makefile` run against `extraction-service`.
- default `make up` now rebuilds an image-backed `extraction-service` so the
  running runtime matches a known source snapshot.
- use `make up-live` or `make dev-up` only when you explicitly want bind-mounted
  edits from `extraction/`, `runtime/`, and `seocho/` reflected immediately.
- keep graph procedure privileges scoped (`apoc.*,n10s.*`) in `docker-compose.yml`.
- default local compose stack is `neo4j + extraction-service + evaluation-interface`.
- when decomposing large files, prefer the internal seam classes documented in
  `docs/INTERNAL_CLASS_DESIGN.md` before introducing new top-level services
- local SDK orchestration extracted from `src/seocho/client.py` should land in
  `src/seocho/local_engine.py` before any broader facade redesign

## Docs Website

- source of truth: `README.md` + `docs/*` in this repository
- publish-critical docs for seocho.blog sync:
  - `docs/README.md`
  - `docs/RUNTIME_DEPLOYMENT.md`
  - `docs/APPLY_YOUR_DATA.md`
  - `docs/PYTHON_INTERFACE_QUICKSTART.md`
  - `docs/TUTORIAL_FIRST_RUN.md`
  - `docs/OPEN_SOURCE_PLAYBOOK.md`
  - `docs/RUN_SPECS.md`
  - `docs/RELEASE_AND_COMMUNITY_OPERATIONS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/WORKFLOW.md`
- repo-side source-doc contract is checked by `.github/workflows/docs-consistency.yml`
  using `bash scripts/ci/check-doc-contracts.sh`
- the tracked website source app lives in `website/`
- current public deployment for `https://seocho.blog` is still
  `tteon/tteon.github.io` GitHub Pages until Pages is enabled on `tteon/seocho`
- `website/scripts/generate-docs.mjs` materializes selected `/docs/*` and
  `/blog/*` pages from repo-root source docs for the in-repo site app
- the `scripts/sync.mjs` helper in `tteon/tteon.github.io` mirrors the selected
  source docs into the live GitHub Pages repository
- generated mirror files under `website/src/content/docs/docs/` are derived
  artifacts; edit the repo-root source docs instead
- website validation currently lives in `.github/workflows/docs-site-quality.yml`
  and includes:
  - `cd website && npm ci`
  - `cd website && npm run check:docs`
  - `cd website && npm run build`
  - `cd website && bash scripts/check-built-links.sh`
- the in-repo deployment workflow is `.github/workflows/docs-site-deploy.yml`,
  but it performs a Pages preflight and skips deployment while Pages is not
  enabled on `tteon/seocho`
- for live docs changes today, also run the Pages repository's sync drift,
  docs quality, static build, and built-link gates before pushing it

5. Basic CI

- workflow: `.github/workflows/ci-basic.yml`
- canonical local command: `bash scripts/ci/run_basic_ci.sh`
- current scope:
  - semantic/runtime/SDK `py_compile`
  - focused semantic/runtime/SDK pytest
  - `git diff --check`
  - `bash scripts/ci/check-runtime-shell-contract.sh`
  - `bash scripts/ci/check-module-ownership-contract.sh`
  - `scripts/pm/lint-agent-docs.sh`

Runtime migration slices should follow `docs/RUNTIME_PACKAGE_MIGRATION.md` and
the runtime shell validation contract in `scripts/ci/check-runtime-shell-contract.sh`.

6. Daily Codex Maintenance Automation

- workflow: `.github/workflows/daily-codex-maintenance.yml`
- cadence: daily at `00:15 UTC` (`09:15 Asia/Seoul`) plus `workflow_dispatch`
- required secrets:
  - `OPENAI_API_KEY`
  - `SEOCHO_GITHUB_APP_ID`
  - `SEOCHO_GITHUB_APP_PRIVATE_KEY`
- if any required secret is missing, the workflow exits successfully after an
  explicit skip notice and creates no PR
- prompt contract: `.github/codex/prompts/daily-maintenance-pr.md`
- PR contract:
  - draft PR only
  - branch `codex/daily-maintenance`
  - run `bash scripts/ci/run_basic_ci.sh` before creating/updating the PR
  - PR body must include `Feature`, `Why`, `Design`, `Expected Effect`,
    `Impact Results`, `Validation`, and `Risks`
  - no direct push to `main`

7. Periodic Codex Review Automation

- workflow: `.github/workflows/periodic-codex-review.yml`
- cadence: weekly on Monday at `00:45 UTC` (`09:45 Asia/Seoul`) plus
  `workflow_dispatch`
- required secrets:
  - `OPENAI_API_KEY`
  - `SEOCHO_GITHUB_APP_ID`
  - `SEOCHO_GITHUB_APP_PRIVATE_KEY`
- if any required secret is missing, the workflow exits successfully after an
  explicit skip notice and creates no PR
- prompt contract: `.github/codex/prompts/periodic-review-pr.md`
- PR contract:
  - draft PR only
  - branch `codex/periodic-review`
  - run `bash scripts/ci/run_basic_ci.sh` before creating/updating the PR
  - PR body must include `Feature`, `Why`, `Design`, `Expected Effect`,
    `Impact Results`, `Validation`, and `Risks`
  - no direct push to `main`

8. Comment-Based Merge Automation

- workflow: `.github/workflows/pr-comment-merge.yml`
- trigger: `issue_comment` on PRs with command exactly `/go`
- authorization:
  - commenter must have repository permission `write`, `maintain`, or `admin`
- merge behavior:
  - PR must be open and not draft
  - PR merge state must be `CLEAN`
  - merge method is `squash` with branch deletion
  - maintainers should mark automation PRs ready for review before `/go`

9. Governance loop
- log architecture decisions as ADRs
- track context graph events and quality metrics
- schedule follow-up issues for unresolved risks
- keep release readiness and open-source community operations aligned with
  `docs/RELEASE_AND_COMMUNITY_OPERATIONS.md`
