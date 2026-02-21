# Decision Log

This file is the lightweight index of architecture/product decisions.
Each entry must link to a full ADR when impact is non-trivial.

## 2026-02-14

- Accepted `ADR-0001-aip-platform-baseline.md`
  - OpenAI Agents SDK as agent runtime
  - Opik for tracing/evaluation
  - DozerDB fixed as backend graph DB
  - Single-tenant MVP, multi-tenant-ready data model
  - `owlready2` allowed only for offline policy validation/compilation

- Accepted `ADR-0002-runtime-guardrails-phase1.md`
  - add `workspace_id` to API/context
  - add runtime policy engine hook for endpoint authorization
  - introduce DozerDB-first config aliases
  - keep ontology reasoning out of request hot path

- Accepted docs restructure (no ADR)
  - separate active docs and archive docs
  - add explicit workflow doc for control plane/data plane visibility
  - make README the primary entry with workflow and doc map links

## 2026-02-15

- Accepted `ADR-0003-rule-api-phase1.md`
  - add `/rules/infer` and `/rules/validate`
  - enforce runtime permission actions for rules APIs
  - keep workspace-aware request contract

- Accepted `ADR-0004-rule-profile-lifecycle-and-cypher-export.md`
  - add rule profile save/list/get APIs
  - add DozerDB Cypher export API for rule profiles
  - return unsupported mapping kinds explicitly in export response

- Accepted `ADR-0005-graph-model-selection-for-upload-flow.md`
  - adopt layered graph representation for upload flow
  - keep Owlready2 in offline ontology governance path
  - align retrieval strategy to local/global/query-structured patterns

- Accepted `ADR-0006-issue-task-governance-for-sprints.md`
  - enforce required collaboration labels on active work items
  - standardize issue/task capture scripts
  - add sprint board and lint tooling for roadmap execution

- Accepted `ADR-0007-agent-docs-baseline-refresh.md`
  - refresh `CLAUDE.md` as primary execution contract
  - refresh `AGENTS.md` as concise operational rules
  - align docs with current stack and workflow guardrails

- Accepted `ADR-0008-agent-doc-lint-automation.md`
  - add `scripts/pm/lint-agent-docs.sh` baseline checks
  - enforce critical agent-doc stack/workflow/link markers

- Accepted `ADR-0009-repository-doc-hygiene-cleanup.md`
  - remove obsolete archived docs from git tracking
  - update docs index archive status
  - ignore local scratch/nested workspace directories

- Accepted `ADR-0010-semantic-agent-flow-with-fulltext-entity-resolution.md`
  - add `/run_agent_semantic` for query-time entity disambiguation path
  - adopt 4-agent query flow (Router/LPG/RDF/AnswerGeneration)
  - standardize fulltext-first entity resolution with semantic reranking

- Accepted `ADR-0011-semantic-fulltext-bootstrap-and-ontology-hint-hook.md`
  - add `/indexes/fulltext/ensure` bootstrap endpoint
  - consume offline ontology hints for alias/label-aware reranking
  - expose semantic execution mode in Agent Studio

- Accepted `ADR-0012-semantic-override-loop-and-offline-owlready2-hints.md`
  - add `entity_overrides` contract to `/run_agent_semantic`
  - add Agent Studio candidate selection + rerun loop
  - add owlready2 offline hint builder script

- Accepted `ADR-0013-custom-interactive-chat-platform-replaces-streamlit.md`
  - replace Streamlit evaluation path with custom frontend backend
  - add `/platform/chat/*` API contracts
  - introduce backend/frontend specialist orchestration layer

## 2026-02-20

- Accepted reliability hardening patch (no ADR)
  - align `Makefile` quality-gate targets with active Compose service (`extraction-service`)
  - remove wildcard Neo4j procedure unrestricted setting from compose
  - stabilize API tests with `httpx.ASGITransport` and module-safe mocks
  - harden sprint lint execution with `bd --no-daemon`

- Accepted SHACL-like practical readiness API addition (no ADR)
  - add `/rules/assess` combining validation results and exportability checks
  - provide actionable readiness status (`ready|caution|blocked`) for real rollout decisions
  - add local demo script and practical guide documentation

- Accepted docs website dispatch design (no ADR, rollout pending owner permission)
  - define docs push trigger contract (`seocho-docs-sync`) for `tteon/tteon.github.io`
  - require repository-owner credential path for workflow-scope permissions

- Accepted `ADR-0014-seocho-philosophy-charter-and-dag-contract.md`
  - codify SEOCHO philosophy as explicit design and operating charter
  - formalize backend topology metadata as frontend DAG rendering contract
  - require philosophy alignment checks in workflow and agent implementation

- Accepted `ADR-0015-philosophy-feasibility-review-framework.md`
  - add multi-role feasibility review framework tied to philosophy charter
  - standardize Go/Conditional Go/No-Go rubric with role-specific checklists
  - require architecture-significant intake to run feasibility review

- Accepted `ADR-0016-runtime-raw-ingest-and-local-verification-path.md`
  - add runtime raw ingest endpoint and UI controls for ingestion-to-chat verification
  - add fallback extraction path for local validation when LLM extraction is unavailable
  - make extraction host ports configurable and tighten DB routing/loading behavior

- Accepted `ADR-0017-runtime-e2e-smoke-gate-for-ingest-chat-flow.md`
  - add dockerized runtime e2e smoke checks for ingest->semantic/debate chat paths
  - enforce CI workflow gate for integration-level regressions
  - add local execution target (`make e2e-smoke`) for reproducible validation

## 2026-02-21

- Accepted `ADR-0018-user-activation-priority-and-docs-sync-contract.md`
  - define user activation critical path as release gate (raw ingest -> semantic/debate chat -> strict e2e)
  - formalize architecture execution order (P0/P1/P2) for runtime reliability and governance
  - enforce docs sync contract for seocho.blog source documents

- Accepted `ADR-0019-agent-sdk-adapter-and-debate-readiness-contract.md`
  - isolate Agent SDK run/trace calls behind adapter for signature compatibility
  - expose debate agent readiness (`agent_statuses`, `degraded`) for partial availability handling
  - add contract tests for adapter and readiness behavior

- Accepted `ADR-0020-p1-elementid-health-split-and-readiness-fallback.md`
  - migrate semantic runtime queries to `elementId(...)` contract path
  - add split health endpoints (`/health/runtime`, `/health/batch`)
  - add readiness-state fallback from blocked debate mode to semantic mode

- Accepted `ADR-0021-non-hydra-runtime-config-and-ingestion-loader.md`
  - remove Hydra/OmegaConf from active runtime and batch execution paths
  - standardize env-first YAML config loading in centralized `extraction/config.py`
  - keep Opik as tracing/evaluation layer (separate concern from configuration)

- Accepted `ADR-0022-shacl-artifact-export-and-batch-rule-aggregation.md`
  - add SHACL-compatible export endpoint (`/rules/export/shacl`) with Turtle + shape JSON
  - keep dual-governance export path with existing Cypher constraints endpoint
  - infer/apply rules at runtime ingest batch scope and return inferred `rule_profile` for traceability

- Accepted `ADR-0023-runtime-three-pass-semantic-ingest-and-relatedness-gate.md`
  - add heterogeneous raw material parser layer (`text`/`csv`/`pdf`) in runtime ingest path
  - add LLM 3-pass semantic extraction (ontology candidate -> SHACL candidate -> entity extraction)
  - add relatedness gate for linking decisions and return semantic artifact summaries in ingest response

- Accepted `ADR-0024-ocr-fallback-embedding-relatedness-and-artifact-approval-gate.md`
  - add OCR fallback path for scanned PDF ingest when direct text extraction is empty
  - extend relatedness with optional embedding score for linking decisions
  - add semantic artifact approval policy (`auto`, `draft_only`, `approved_only`) for governance-safe rollout

- Accepted `ADR-0025-semantic-artifact-draft-approval-lifecycle-api.md`
  - add semantic artifact lifecycle endpoints for draft save/list/read/approve
  - add server-side approved artifact resolution via `approved_artifact_id` in runtime ingest
  - enforce dedicated permission action (`manage_semantic_artifacts`) for artifact governance operations

## Template

Use this block for new entries:

```md
## YYYY-MM-DD

- [Status] ADR-XXXX short-title
  - key decision 1
  - key decision 2
  - risk/tradeoff note
```
