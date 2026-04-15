# Decision Log

This file is the lightweight index of architecture/product decisions.
Each entry must link to a full ADR when impact is non-trivial.

## 2026-04-13

- Accepted `ADR-0049-pipeline-unification-canonical-modules.md`
  - core logic (rules, embedding linker, vector store) moves into `seocho/` as canonical
  - `extraction/` modules become re-export shims or adapter shims
  - parity harness (`tests/test_parity_harness.py`) guards local ↔ server result contract
  - local mode now produces `rule_profile` + `relatedness_summary` matching server contract

## 2026-04-15

- Accepted `ADR-0062-staged-runtime-package-rename.md`
  - choose `runtime/` as the long-term deployment-shell package name
  - keep `seocho/` as canonical engine owner
  - reduce `extraction/` toward extraction-only concerns or compatibility wrappers through staged migration

- Accepted `ADR-0063-benchmark-track-split-and-finder-baseline-first-slice.md`
  - split benchmark work into `FinDER` and `GraphRAG-Bench` tracks
  - measure SEOCHO local SDK before SEOCHO runtime before peer systems
  - ship a first-slice FinDER baseline harness for repeatable local/runtime measurements

- Accepted `ADR-0064-runtime-package-first-shell-slice.md`
  - introduce `runtime/` as the canonical deployment-shell package in code, not
    only in planning docs
  - move `agent_server`, `server_runtime`, `policy`, and `public_memory_api`
    ownership under `runtime/`
  - keep flat `extraction/*` imports working through module-alias compatibility shims

## 2026-03-12

- Accepted `ADR-0028-graph-registry-and-multi-instance-debate-runtime.md`
  - add graph-scoped registry descriptors (`graph_id -> uri/database/ontology/vocabulary`)
  - support one OpenAI Agents SDK specialist per graph target, including multi-instance Neo4j routing
  - expose `GET /graphs` and `graph_ids`-based debate scoping as the runtime contract

- Accepted `ADR-0027-public-graph-memory-facade-and-document-intake-contract.md`
  - expose memory-first public APIs on top of SEOCHO runtime
  - standardize runtime provenance around `Document` nodes with shared `memory_id`
  - adopt SKOS-compatible `vocabulary.v2` artifacts for runtime vocabulary exchange
  - require `DEV-*` document prefixes as the coding-agent intake contract

## 2026-03-02

- Accepted `ADR-0026-enterprise-vocabulary-layer-global-access.md`
  - derive governed vocabulary candidates from entity extraction/linking and SHACL-like artifacts
  - expose global approved vocabulary with `workspace_id`-scoped override resolution
  - align lifecycle with semantic governance (`draft -> approved -> deprecated`)
  - keep heavy ontology reasoning offline (`owlready2` path), with lightweight runtime lookup/expansion only

## 2026-02-14

- Accepted `ADR-0001-aip-platform-baseline.md`
  - OpenAI Agents SDK as agent runtime
  - observability wording later superseded by ADR-0045 (vendor-neutral trace contract, Opik preferred backend)
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

## 2026-03-13

- Accepted `ADR-0030-local-bootstrap-cli-and-artifact-governance-helpers.md`
  - add `seocho serve` and `seocho stop` as repository-local bootstrap commands
  - allow fallback local `OPENAI_API_KEY` injection when env values are missing or still placeholders
  - add local semantic artifact `validate` / `diff` / `apply` helpers in the SDK and CLI

- Accepted `ADR-0029-typed-semantic-prompt-context-and-artifact-expert-surface.md`
  - add typed SDK models for semantic prompt context and approved artifact payloads
  - expose semantic artifact lifecycle operations in the official SDK and CLI
  - standardize runtime prompt precedence as graph metadata -> approved artifacts -> request overrides -> runtime drafts

- Accepted durable rule profile registry migration (no ADR)
  - replace filesystem JSON profile store with SQLite registry (`RULE_PROFILE_DIR/rule_profiles.db` by default)
  - add workspace-scoped `profile_version` sequencing and retention cap (`RULE_PROFILE_RETENTION_MAX`)
  - keep compatibility by importing legacy JSON profiles on first workspace access

## 2026-04-09

- Accepted `ADR-0031-intent-first-graph-rag-evidence-bundle-contract.md`
  - move semantic graph answering toward `intent_id -> evidence bundle -> grounded answer`
  - define answerability in terms of required relations, entity types, and slot fills
  - require missing-slot visibility and fixed-answerer evaluation fairness

- Accepted `ADR-0032-daily-codex-github-app-maintenance-workflow.md`
  - add repo-local Codex skill + prompt for daily maintenance PR generation
  - run scheduled Codex automation in GitHub Actions with a GitHub App token
  - keep the automation review-first with draft PRs and no direct push to `main`

- Accepted `ADR-0033-public-python-sdk-and-pip-distribution-contract.md`
  - broaden the public Python package from memory-only helpers to full runtime SDK surfaces
  - add module-level convenience API (`seocho.ask`, `seocho.chat`, `seocho.debate`, `seocho.configure`)
  - make default package dependencies lightweight for public `pip install` usage

- Accepted `ADR-0034-python-package-publish-and-periodic-codex-review-workflows.md`
  - add GitHub Actions publish flow for TestPyPI/PyPI with build and `twine check`
  - add a separate periodic Codex draft-PR workflow for bounded refactors and small improvements
  - keep scheduled improvement automation review-first with no direct push to `main`

## 2026-04-13

- Accepted `ADR-0048-canonical-query-engine-first-slice.md`
  - introduce canonical query engine modules under `seocho/query/`
  - move local planner/executor/answer-synthesis responsibilities behind shared query contracts
  - reuse canonical evidence-bundle shaping from server runtime paths

## 2026-04-13

- Accepted `ADR-0046-core-compose-stack-and-onboarding-artifact-contract.md`
  - default local compose stack is `neo4j + extraction-service + evaluation-interface`
  - move standalone `semantic-service` to an explicit legacy profile
  - require onboarding docs to distinguish HTTP client vs local engine vs local runtime modes
  - document local ontology, graph, rule, semantic-artifact, and trace file locations

- Accepted `ADR-0047-thin-http-install-and-local-extra-contract.md`
  - keep `pip install seocho` as the thin HTTP client path
  - add `seocho[local]` as the published-package local SDK engine path
  - make top-level `seocho` exports lazy so optional runtime deps are not eagerly imported
  - require website and source docs to share the same install/runtime split

## 2026-04-12

- Accepted `ADR-0045-vendor-neutral-tracing-and-explicit-opik-opt-in.md`
  - define the runtime tracing contract as `none|console|jsonl|opik`
  - treat JSONL as the canonical neutral trace artifact and Opik as the preferred team backend
  - require explicit Opik enablement before wrapping SDK OpenAI clients or activating Opik exporter paths

- Accepted `ADR-0043-ontology-governance-cli-and-owlready2-boundary.md`
  - tighten README ontology lifecycle wording so it matches actual runtime behavior
  - add offline `seocho ontology` governance commands for check, export, diff, and optional OWL inspection
  - keep Owlready2 optional and outside the request hot path

- Accepted `ADR-0044-ontology-package-lineage-and-migration-warning-contract.md`
  - split ontology lineage (`package_id`) from ontology release version
  - emit semver-aware migration warnings from ontology diff output
  - keep guidance conservative and offline-governance focused

- Accepted `ADR-0041-portable-sdk-runtime-bundle-and-http-adapter.md`
  - add a portable bundle contract for SDK-authored local runtimes
  - expose a narrow FastAPI adapter so other developers can consume SDK-authored apps over HTTP
  - keep portability declarative and reject custom Python hooks

- Accepted `ADR-0042-openai-compatible-provider-and-vector-backend-contract.md`
  - add OpenAI-compatible provider presets for OpenAI, DeepSeek, Kimi, and Grok
  - expose Agents SDK helper builders from the same provider-backed SDK objects
  - add LanceDB as a persistent vector backend alongside FAISS

## 2026-04-12

- Accepted `ADR-0041-portable-sdk-runtime-bundle-and-http-adapter.md`
  - add a portable bundle contract for SDK-authored local runtimes
  - expose a small HTTP adapter so other developers can consume those apps with normal HTTP client mode
  - keep the portable runtime declarative-only and narrower than the full main server runtime

- Accepted `ADR-0035-comment-triggered-maintainer-merge-workflow.md`
  - add `/go` comment-triggered squash merge workflow for reviewed PRs
  - require `write`/`maintain`/`admin` permission before merge automation runs
  - keep branch protection and required checks as the final merge gate

## 2026-04-11

- Accepted `ADR-0036-documentation-consistency-ci-contract.md`
  - add repo-side docs contract checks for active source documentation
  - keep website docs quality and mirrored-doc drift checks split into the website repo
  - reject stale runtime endpoint examples and stale sync wording before publish
- Accepted `ADR-0037-semantic-support-validation-and-strategy-metadata-contract.md`
  - emit explicit semantic `support_assessment`, `strategy_decision`, and `run_metadata`
  - upgrade runtime grounding payloads to `evidence_bundle.v2`
  - keep debate as an opt-in advanced path while making escalation recommendations explicit
- Accepted `ADR-0038-semantic-registry-evaluation-and-profile-packages.md`
  - replace ad hoc semantic run output with a SQLite-backed queryable registry
  - add SDK-level manual-gold evaluation over question/reference/semantic baselines
  - add deterministic profile packages and disagreement-aware advanced recommendations
- Accepted `ADR-0039-remove-broken-repo-github-actions.md`
  - remove all repository-local GitHub Actions workflows for now
  - make local validation the active delivery path again
  - require any future repo automation to return through a fresh ADR and working rollout
- Accepted `ADR-0040-working-basic-ci-and-codex-pr-automation.md`
  - restore a narrow working `ci-basic.yml` backed by `scripts/ci/run_basic_ci.sh`
  - restore bounded daily/periodic Codex draft PR workflows on top of that CI
  - restore maintainer-triggered `/go` squash merge gated on clean PR state
  - require a fixed PR body contract for automation-generated maintenance/review PRs

## 2026-04-13

- Accepted `ADR-0048-canonical-query-engine-first-slice.md`
  - introduce canonical query engine modules under `seocho/query/`
  - move local planner/executor/answer-synthesis responsibilities behind shared query contracts
  - reuse canonical evidence-bundle shaping from server runtime paths

- Accepted `ADR-0049-canonical-agent-engine-first-slice.md`
  - introduce canonical agent modules under `seocho/agent/`
  - move session context and agent factory logic behind the canonical agent package
  - keep `seocho.agents` as a compatibility shim while local runtime migrates

- Accepted `ADR-0050-canonical-ontology-subdomains-first-slice.md`
  - split ontology internals into explicit serialization, artifact, and governance boundaries
  - keep `Ontology` as the stable public facade while internal helpers take over implementation
  - make runtime artifact generation depend on ontology-side contracts instead of client glue

- Accepted `ADR-0051-client-facade-boundary-first-slice.md`
  - extract HTTP transport and ontology artifact bridge helpers out of `client.py`
  - keep `Seocho` as the stable facade while canonical engines move underneath it
  - defer `_LocalEngine` extraction to a later slice

- Accepted `ADR-0052-agent-server-runtime-service-split-first-slice.md`
  - extract shared runtime service composition into `extraction/server_runtime.py`
  - make public memory router composition lazy so server import does not force memory-service construction
  - keep endpoint contracts stable while shrinking `agent_server.py`

- Accepted `ADR-0053-extraction-cleanup-vector-shim-first-slice.md`
  - replace `extraction/vector_store.py` with a compatibility adapter over canonical SEOCHO vector primitives
  - classify extraction modules as shim now, keep as transport/composition, or migrate later
  - leave larger ingestion canonicalization to follow-up slices

- Accepted `ADR-0054-extraction-pipeline-canonical-engine-first-slice.md`
  - introduce `seocho/index/extraction_engine.py` as a shared extraction/linking seam
  - make `seocho/index/pipeline.py` and `extraction/pipeline.py` share canonical prompt and normalization logic
  - keep `runtime_ingest.py` out of scope for this slice

- Accepted `ADR-0055-runtime-ingest-canonical-extraction-seam-first-slice.md`
  - move runtime ingest prompt-driven extraction and linking setup onto the canonical SEOCHO extraction seam
  - keep compatibility adapters so the semantic orchestrator can continue calling legacy extractor/linker method names
  - leave the larger runtime_ingest orchestration split for later slices

- Accepted `ADR-0056-canonicalize-semantic-query-flow-to-sdk.md`
  - move SemanticAgentFlow and 14 supporting classes from extraction/semantic_query_flow.py to seocho/query/*
  - rationale: industry survey (Graphiti, Cognee, mem0, LlamaIndex, Neo4j GraphRAG) confirms DB-stateful query orchestration belongs in SDK
  - extraction/agent_server.py becomes thin wrapper, mirroring Graphiti's server/graph_service/routers pattern
  - 4-phase migration (pure logic → DB-aware support → agents → SemanticAgentFlow), each gated by parity harness

- Accepted `ADR-0057-runtime-ingest-deterministic-helper-seams-first-slice.md`
  - extract runtime memory-graph shaping and semantic-artifact merge helpers into canonical `seocho/index/*` modules
  - keep `RuntimeRawIngestor` static and instance helper wrappers stable while delegating implementation to canonical helpers
  - leave runtime-only orchestration, embedding-relatedness I/O, and DB loading flow in `runtime_ingest.py` for later slices

- Accepted `ADR-0058-semantic-query-phase-a-pure-logic-first-slice.md`
  - move semantic query pure-logic primitives into canonical `seocho/query/*` modules
  - rebind `extraction/semantic_query_flow.py` runtime helpers to canonical classes while keeping the existing import surface stable
  - defer DB-aware helpers, route agents, and `SemanticAgentFlow` itself to later ADR-0056 phases

- Accepted `ADR-0059-semantic-query-phase-b-db-aware-support-first-slice.md`
  - move semantic query constraint-slice building and semantic run metadata persistence into canonical `seocho/query/*` modules
  - rebind `extraction/semantic_query_flow.py` runtime instances to canonical support classes while keeping the existing import surface stable
  - defer route agents and `SemanticAgentFlow` itself to later ADR-0056 phases

- Accepted `ADR-0060-semantic-query-phase-c-route-agents-first-slice.md`
  - move semantic query route-agent classes into canonical `seocho/query/semantic_agents.py`
  - rebind `extraction/semantic_query_flow.py` route-agent names to canonical implementations while keeping the existing import surface stable
  - defer `SemanticAgentFlow` itself to the final ADR-0056 phase

- Accepted `ADR-0061-semantic-query-phase-d-flow-first-slice.md`
  - move `SemanticAgentFlow` orchestration into canonical `seocho/query/semantic_flow.py`
  - rebind `extraction/semantic_query_flow.py` to the canonical flow class while keeping the existing import surface stable
  - keep runtime graph-target injection in the extraction shell

- Accepted `ADR-0064-runtime-package-first-shell-slice.md`
  - introduce `runtime/` as the canonical deployment-shell package
  - keep `extraction/agent_server.py`, `extraction/server_runtime.py`, `extraction/policy.py`, and `extraction/public_memory_api.py` as compatibility aliases
  - normalize repo-owned tests and docs toward `runtime/*` imports first

- Accepted `ADR-0065-runtime-ingest-runtime-package-slice.md`
  - move `RuntimeRawIngestor` ownership to `runtime/runtime_ingest.py`
  - keep `extraction/runtime_ingest.py` as a compatibility alias during staged rename work
  - continue shrinking runtime ingest toward deployment-shell composition while preserving current API behavior

- Accepted `ADR-0066-runtime-migration-automation-guardrails.md`
  - add a repo-local Codex skill for bounded runtime migration slices
  - add a fast runtime-shell contract check for active docs/tests/imports
  - wire the contract check into basic CI and repo-managed pre-commit flow

- Accepted `ADR-0067-runtime-support-module-slice.md`
  - move runtime support ownership for readiness, request middleware, and memory facade under `runtime/`
  - keep `extraction/agent_readiness.py`, `extraction/middleware.py`, and `extraction/memory_service.py` as compatibility aliases
  - update active tests, docs, CI, and runtime-shell contract checks to prefer canonical `runtime/*` paths

- Accepted `ADR-0068-ontology-context-cache-and-agent-middleware-seam.md`
  - introduce compact shared ontology context descriptors and cache under `seocho/ontology_context.py`
  - attach `ontology_context_hash` metadata across local indexing, query traces, and agent session context
  - include SKOS-style glossary/vocabulary hash in the context identity
  - defer Rust/DataBook-style portable bundles until the Python SDK contract is stable and measured

- Accepted `ADR-0069-ontology-context-graph-write-and-query-guardrail.md`
  - persist compact `_ontology_*` properties on local SDK graph write payloads
  - compare active ontology context hash with indexed graph context hashes at query time
  - surface mismatch metadata in local query traces and agent query tool output without blocking reads

- Accepted `ADR-0070-runtime-ontology-context-response-contract.md`
  - expose `ontology_context_mismatch` through runtime memory search/chat and semantic query responses
  - parse the metadata into typed Python SDK response objects for direct library-user access
  - attach runtime graph target ontology metadata during runtime ingest without fabricating SDK context hashes

- Accepted `ADR-0071-runtime-agent-ontology-middleware-contract.md`
  - expose `ontology_context_mismatch` as a top-level typed field on router, debate, platform chat, and execution-plan responses
  - resolve router `graph_ids` into database-scoped agent tool contexts so graph selection affects both DB access and parity metadata
  - keep ontology/database parity as lightweight middleware metadata instead of adding hot-path ontology reasoning

## Template

Use this block for new entries:

```md
## YYYY-MM-DD

- [Status] ADR-XXXX short-title
  - key decision 1
  - key decision 2
  - risk/tradeoff note
```
