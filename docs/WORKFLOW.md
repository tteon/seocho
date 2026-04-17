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
- `docs/BEADS_OPERATING_MODEL.md`

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

- `seocho/rules.py` — canonical rule inference/validation
- `seocho/index/pipeline.py` — canonical indexing with `enable_rule_constraints` + `embedding_backend`
- `seocho/index/linker.py` — canonical embedding-based entity linker
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
- capture work item using standardized scripts:
  - `scripts/pm/new-issue.sh`
  - `scripts/pm/new-task.sh`
- keep `.beads` as the canonical planning/status tracker
- if the change touches a shared seam, create a Gastown reservation after the
  `bd` item exists:
  - include seam id, owner, `bd` id, branch/worktree, write scope, and TTL
  - use `.agents/gastown/shared-seams.yaml` as the repo seam registry
  - treat Gastown as coordination only, not a second planning system
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
- optional one-command landing wrapper: `scripts/land.sh --task-id <id> --fix --pull --push`
- run sprint label lint (`scripts/pm/lint-items.sh --sprint <id>`)
- run agent docs lint (`scripts/pm/lint-agent-docs.sh`)
- release or hand off any Gastown reservation before merge
- close issue, rebase, sync, push
- verify branch is up to date with origin

Operational notes:

- use `scripts/pm/lint-items.sh` with internal `bd --no-daemon` execution to avoid local daemon startup stalls.
- when using git worktrees, prefer `BEADS_NO_DAEMON=1` to prevent daemon writes
  landing in the wrong worktree
- current dev quality gates in `Makefile` run against `extraction-service`.
- while the service name remains `extraction-service`, compose mounts
  `runtime/` and `seocho/` into `/app` so legacy flat entrypoints can import
  canonical runtime and SDK modules during the staged rename.
- keep graph procedure privileges scoped (`apoc.*,n10s.*`) in `docker-compose.yml`.
- default local compose stack is `neo4j + extraction-service + evaluation-interface`.
- legacy `semantic-service` is opt-in only via `docker compose --profile legacy-semantic up -d semantic-service`.
- when decomposing large files, prefer the internal seam classes documented in
  `docs/INTERNAL_CLASS_DESIGN.md` before introducing new top-level services

## Docs Website Sync

- source of truth: `README.md` + `docs/*` in this repository
- publish-critical docs for seocho.blog sync:
  - `docs/README.md`
  - `docs/QUICKSTART.md`
  - `docs/APPLY_YOUR_DATA.md`
  - `docs/PYTHON_INTERFACE_QUICKSTART.md`
  - `docs/ARCHITECTURE.md`
  - `docs/WORKFLOW.md`
- repo-side source-doc contract is checked by `.github/workflows/docs-consistency.yml`
  using `bash scripts/ci/check-doc-contracts.sh`
- website updates are maintained directly in the `tteon.github.io/` workspace
- website validation currently includes:
  - `npm run check:sync`
  - `bash scripts/check-doc-quality.sh`
  - `npm run build`
  - `bash scripts/check-built-links.sh`
- `tteon.github.io/scripts/sync.mjs` can be used as a local helper when syncing
  selected docs, but mirrored pages are still reviewable content, not a blind
  publish target

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

Runtime migration slices should use the repo-local skill:

- `.agents/skills/runtime-migration-slice/SKILL.md`

Install repo-managed hooks once per clone:

```bash
scripts/pm/install-git-hooks.sh
```

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
- skill contract: `.agents/skills/daily-maintenance-pr/SKILL.md`
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
- skill contract: `.agents/skills/periodic-review-pr/SKILL.md`
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
