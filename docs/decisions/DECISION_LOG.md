# Decision Log

This file is the lightweight index of architecture/product decisions.
Each entry must link to a full ADR when impact is non-trivial.

## 2026-07-13

- [Accepted] `ADR-0152-sdcr-evidence-swarm-coordinator.md`
  - execute the smallest authorized SDCR coalition with bounded parallelism
  - assemble one protected/conflict-aware typed evidence bundle for synthesis
  - separate deterministic retrieval load from bounded live MARA answering

- [Accepted] `ADR-0150-connector-materialization-layer.md`
  - add a read-only connector materialization layer that writes
    `seocho.connector_record.v1` JSONL for the existing index -> query ->
    report path
  - ship first adapters for Notion, Slack, DataHub, PostgreSQL, Neo4j/DozerDB,
    plus LangChain/LlamaIndex document converters without framework dependencies
  - add `seocho.connectors.yaml` and a content-free state artifact for
    repeatable multi-source materialization
  - keep API credentials out of run specs and require live service runs before
    claiming external compatibility, latency, or throughput

- [Proposed] `ADR-0151-versioned-causal-frontiers-and-sequence-leasing.md`
  - keep the gapless scalar v1 workspace order as the compatible default
  - add opt-in fenced leases, deterministic shards, and multi-position causal
    frontiers without treating reserved gaps as committed events
  - qualify Rust only against equal revision/idempotency/outbox semantics and
    expose bounded commit-phase latency in Grafana

## 2026-07-12

- [Status note] `ADR-0089-in-repo-docs-site-and-pages-deploy.md`
  - in-repo site source and quality gates are active in `website/`
  - current public `seocho.blog` deployment still runs from
    `tteon/tteon.github.io` until Pages is enabled on `tteon/seocho`
  - live docs changes must be mirrored and validated in the Pages repository
    during the transition

## 2026-07-11

- [Accepted] `ADR-0146-production-observability-profiles-and-metric-contract.md`
  - make production observability a supported SEOCHO capability with core,
    dependency, cluster, TLS, and evaluation enablement profiles
  - separate production SLO paging from first-class blockchain evaluation
    scorecards while provisioning both surfaces
  - require bounded labels, capability-truthful signals, derived recording
    rules, privacy/cardinality gates, and live OTLP verification

- [Proposed] `ADR-0145-blockchain-long-term-memory.md`
  - keep append-only canonical/orphaned blockchain event revisions in an
    authoritative transactional memory plane
  - update risk aggregates and graph outbox entries in the same transaction
  - keep DozerDB rebuildable, etcd coordination-only, and the FoundationDB
    native client optional behind a tested transaction-runner contract

## 2026-07-10

- [Proposed] `ADR-0147-otlp-and-query-workload-observability.md`
  - add optional OTLP export and nested spans without changing JSONL/Opik
  - default to content-free, privacy-safe prompt and graph-query telemetry
  - make `withdrawal_explanation.v1` the first bounded OKX-style workload;
    defer FoundationDB, etcd, and LiteLLM deployment until effects are measurable

## 2026-06-17

- [Proposed] ADR-0144 local-otel-observability-and-span-trace-structure
  - extends ADR-0045: add `otlp` tracing backend → lightweight local stack
    (OTel Collector + Tempo + Prometheus + Grafana, 4 containers) as the
    alternative to heavy self-hosted Opik; Opik stays the cloud team backend,
    JSONL stays canonical. LMCache's topology was the pattern source; its
    metrics don't apply (no local vLLM+LMCache serving)
  - replace the single flat `sdk.query` span with a nested `rag.ask` tree
    (decompose → arbitrate → compile_cypher → execute → retrieve_ctx →
    synthesize) using existing `StageTimer` + `SessionTrace`; `retrieve_ctx`
    (what was fed to the LLM) is new and top-priority
  - external-API deployment → prompt is the control surface: adopt `gen_ai.*`
    conventions + surface `prompt_version` and `stable_prefix_hash`;
    content-capture (full prompt/Cypher/records) gated behind
    `SEOCHO_TRACE_CAPTURE_CONTENT` (root fix for "Opik felt heavy")
  - `workspace_id` first-class on every span; `rag.execute` splits
    server vs hydration time + `db.client.codec` resource attr, making the
    ADR-0111 neo4j-rust-ext hydration win observable/regression-guarded

## 2026-06-16

- [Proposed] ADR-0143 full-corpus-finder-profile
  - profile the entire FinDER corpus once (5,654 docs, open extraction, MARA
    DeepSeek-V3.1) → canonical financial `corpus_profile` artifact
  - re-rank guardrail candidates at full scale: selector picks
    `fibo_FND_stable` (coverage 0.876) over hand-curated `curated_plus` (0.712);
    stable multi-model 2-pass FIBO bridge corroborates ADR-0140 at full scale
  - honest: coverage is a selection proxy — answer accuracy stays equivalent
    (ADR-0141); concludes the FIBO arc 0132→0143

## 2026-06-14

- Accepted `ADR-0132-fibo-upstream-submodule-and-compiled-artifact-contract.md` (renumbered from ADR-0112 — collided with the graph-rag ADR-0112)
  - add official EDM Council FIBO as pinned `third_party/fibo` submodule
  - keep FIBO as an offline source snapshot; runtime consumes compiled
    manifest/catalog/compatibility artifacts only
  - add `scripts/ontology/compile_fibo_snapshot.py` to emit upstream commit,
    snapshot hash, imports, label/definition catalog, and curated-slice
    compatibility report for benchmark-gated ontology promotion

## 2026-06-13

- [Accepted] ADR-0113 track-claude-skills-in-repo
  - version-control shared Claude Code skills under `.claude/skills/` while the
    rest of `.claude/` (local agent/editor state) stays untracked; gitignore
    re-includes only that subtree and the root-hierarchy contract gained a
    skills-scoped exception so anything else under `.claude/` still fails CI
  - first skill landed: `seocho-e2e` (author + run `seocho run` / `seocho sweep`)

- [Proposed] ADR-0112 graph-rag-where-it-wins-governance-not-quality
  - synthesis of the content-vs-context arc: retrieval-quality TIE (graph ≈ vector)
    generalizes across 2 GraphRAG-Bench domains (novel + medical, 2-judge); the
    typed-structure layer is neutral-to-noise for narrative QA once raw text is
    served at a fair budget (ADR-0105 confound fixed + generalized)
  - graph's real differentiator = governance/determinism, measured: declared-schema
    gate refuses out-of-schema structurally (finance arbiter silent-wrong 0/8; BC3
    relation-gate eliminated 69% E4 silent-wrong) + LLM-free provenance aggregation
    vector can't produce — but a well-grounded capable LLM also avoids fabrication
    (0/8), so the gate's value is a guarantee for ungrounded/weak/adversarial cases
    (closed-book vector fabricated 3/8), not a quality win over good RAG
  - decision: stop benchmarking graph as "better QA accuracy" (ties by design);
    evaluate on governance/refusal + provenance/LLM-free-aggregation + entailment
    (LUBM/ProofWriter, NOT YET measured); make the graph serializer relevance-ranked
  - risk: 2 corpora, small n, entailment axis unmeasured

## 2026-06-12

- Accepted `ADR-0111-neo4j-rust-ext-adoption.md`
  - §21 gate run for "switch to the Rust neo4j driver?": measured A/B behind
    the real federation caller — `neo4j-rust-ext` (official Rust PackStream
    codec) 1.62× on the live 3-shard read, 3.57× on 130k-row bulk hydration,
    byte-identical parity → ADOPT (pinned dep replacing bare `neo4j`,
    one-time codec liveness log in `Neo4jGraphStore`, CI golden parity test).
  - `neo4rs` (pure-Rust driver) rejected as a port target (rc-quality, no
    Python bindings, no element_id accessor); 947 ms W2 ceiling recorded.
  - GIL verdict for >100-core scaling: threads collapse (0.13 eff @ N=8) in
    BOTH codecs; processes hold 0.62 — scale-out is process/Ray-actor per
    shard, not a Rust driver. Pre-registered predictions were wrong in BOTH
    directions and are graded in the ADR (localhost makes codec share 91%).
## 2026-06-11

- [Proposed] ADR-0105 graphrag-bench-serializer-confound-fair-budget-tie
  - prior "graph < vector" was a SERIALIZATION confound (graph dropped its own
    stored raw text), not extraction recall/ontology — adding raw lifts both
    SEOCHO and Graphiti on both judges (official GraphRAG-Bench, 2-judge)
  - at a FAIR raw budget (`seocho_keepraw_topk` = struct + same top-k chunks as
    vector), graph-as-context TIES vector; typed structure adds ≈0 over raw
  - the earlier `seocho_keepraw` "win" was context quantity (whole-novel dump),
    corrected
  - decision: `keep_raw` is the default; budget-bounded relevance-ranked
    serialization is the product direction; do NOT claim graph-retrieval
    superiority on narrative QA; measure SEOCHO's real differentiator
    (inference/governance) on entailment + Answerability benches, not retrieval QA
  - risk: single corpus Novel-8559, n=25/type (underpowered); harness stays local

## 2026-06-06

- [Proposed] ADR-0104 worktree-isolated runtime boot
  - `make up INSTANCE=<id>` / `seocho serve --instance <id>` boot an isolated
    app tier (offset ports + ephemeral logical DB) against a SHARED neo4j;
    teardown drops only that instance's app project and database
  - canonical derivation in `src/seocho/instance.py` (validated against the
    `^[a-z][a-z0-9]{2,62}$` runtime contract); orchestration in `local.py`
  - tradeoff: shared neo4j failure domain; hash-derived ports over 40 slots can
    collide and is surfaced via `InstanceLayout.collides_with(...)`
  - closes `seocho-6q9.3`, the last child of the observe-loop epic `seocho-6q9`

## 2026-06-03

- Accepted `ADR-0099-ontology-control-plane-as-agentic-middleware-lock-in.md`
  - SEOCHO's defensible layer is ontology-selection middleware between agents
    and graph/model providers, not a proprietary DBMS or foundation model
  - added `seocho.ontology_control_plane` with typed ontology signals,
    reviewable profiles, hot-path compiled profiles, deterministic profile
    selection, and baseline-vs-candidate evaluation
  - follow-up work should persist signals/profiles, expose user review controls,
    and gate profile promotion with MARA/OpenAI-compatible E2E regression
- Accepted `ADR-0108-experiment-backed-route-profile-and-answer-shape-contract.md`
  - ICML FinDER and KDD DataAgent-Bench experiments support treating graph
    evidence as slot/route control first, not as an always-authoritative final
    answer substrate
  - shared `evidence_bundle.v2` now carries `route_profile`,
    `answer_shape`, and `answer_shape_profile`
  - SDK `EvidenceBundle` preserves those fields for runtime clients and
    follow-on insufficiency-gated fallback work

## 2026-05-30

- Accepted `ADR-0107-experiment-data-query-plane-hardening.md`
  - `Neo4jGraphStore.ensure_database(wait_online=True)` polls until DozerDB reports
    ONLINE (async `CREATE DATABASE` no longer causes "Graph not found")
  - `OpikBackend.log_span` passes `end_time` in the single `trace()` call (no
    batching race that nulled name/tags/metadata) + one-time SDK-version-skew warning
  - `cypher_builder` financial lookup is ontology-aware (metric/anchor labels from
    the ontology, parameterized) with soft `ORDER BY` ranking + ticker anchor match
  - locked by no-service regression tests in `scripts/ci/run_basic_ci.sh`
- Evaluation: FinDER judge upgraded to a cross-vendor **panel** (grok + gpt) with
  inter-judge agreement (Cohen's κ) and same-case **paired** win/tie/loss + Wilcoxon
  vs vector; `number_overlap`/`token_f1` demoted to disclosed-secondary metrics.
  See `scripts/benchmarks/EVALUATION_METRICS.md`.
- Proposed (not built; tracked as follow-up in ADR-0107): insufficiency-gated
  retrieval fall-back ladder, fact-vs-reasoning query router, graph-as-context +
  `graph_chunks` mode, extraction-recall probe.

## 2026-04-13

- Accepted `ADR-0110-pipeline-unification-canonical-modules.md`
  - core logic (rules, embedding linker, vector store) moves into `seocho/` as canonical
  - `extraction/` modules become re-export shims or adapter shims
  - parity harness (`tests/test_parity_harness.py`) guards local ↔ server result contract
  - local mode now produces `rule_profile` + `relatedness_summary` matching server contract

## 2026-04-15

- Accepted `ADR-0062-staged-runtime-package-rename.md`
  - choose `runtime/` as the long-term deployment-shell package name
  - keep `seocho/` as canonical engine owner
  - reduce `extraction/` toward extraction-only concerns or compatibility wrappers through staged migration

- Accepted `ADR-0063-benchmark-track-split-and-private-finance-corpus-contract.md`
  - split benchmark work into `private finance corpus` and `GraphRAG-Bench` tracks
  - measure SEOCHO local SDK before SEOCHO runtime before peer systems
  - ship a finance-domain benchmark harness for repeatable local/runtime measurements

- Accepted `ADR-0064-runtime-package-first-shell-slice.md`
  - introduce `runtime/` as the canonical deployment-shell package in code, not
    only in planning docs
  - move `agent_server`, `server_runtime`, `policy`, and `public_memory_api`
    ownership under `runtime/`
  - keep flat `extraction/*` imports working through module-alias compatibility shims

- Accepted `ADR-0074-runtime-flat-entrypoint-compatibility.md`
  - keep `runtime/` as canonical owner while preserving legacy flat
    `extraction/*` imports from the `extraction/` working directory
  - add an explicit alias bootstrap helper and compose mounts for `runtime/`
    and `seocho/`
  - extend the runtime shell contract gate to cover this staged rename seam

- Accepted `ADR-0075-embedded-local-default-install-contract.md`
  - make `Seocho.local(ontology)` the serverless hello-world path through
    embedded LadybugDB
  - include the embedded graph dependency in `seocho[local]`
  - keep DozerDB/Neo4j as the production graph path; LadybugDB is the embedded
    local default

- Accepted `ADR-0076-local-compose-bind-host-and-password-contract.md`
  - bind compose-published local service ports to `127.0.0.1` by default
  - require an explicit `NEO4J_PASSWORD` instead of falling back to `password`
  - make LAN exposure an explicit `SEOCHO_BIND_HOST=0.0.0.0` opt-in

## 2026-04-17

- Accepted `ADR-0077-gastown-shared-seam-coordination-plane.md`
  - keep `.beads` as the canonical planning/status tracker
  - use Gastown only for shared-seam reservations and handoff coordination
  - add a repo-local shared seam registry with default 24-hour TTL guidance

- Accepted `ADR-0078-secretless-skip-for-scheduled-codex-automation.md`
  - keep `Basic CI` as the required repository quality gate
  - make scheduled Codex workflows emit an explicit skip notice instead of
    failing when automation secrets are absent
  - keep the Codex/App-token execution path unchanged when secrets are present

- Accepted `ADR-0079-basic-ci-module-ownership-contract.md`
  - extend `Basic CI` with module ownership checks for indexing and extraction
    shim seams
  - keep `seocho/*` and `runtime/*` as canonical owners while asserting that
    extraction shims stay thin
  - pull focused ownership regression tests into the default CI surface

- Accepted `ADR-0080-internal-orchestration-seams-for-modular-monolith.md`
  - introduce `DomainEvent`, `IngestionFacade`, `QueryProxy`, `AgentFactory`,
    and `AgentStateMachine` as internal decomposition seams
  - keep `seocho/client.py` as the public facade while logic moves behind it
  - treat these seams as modular-monolith structure, not a microservice split

- Accepted `ADR-0081-local-engine-module-behind-client-facade.md`
  - move `_LocalEngine` into `seocho/local_engine.py`
  - keep `seocho/client.py` as a composition-oriented public facade
  - enforce the boundary with module-ownership checks

- Accepted `ADR-0082-client-remote-and-bundle-helpers.md`
  - move remote transport setup into `seocho/client_remote.py`
  - move runtime bundle glue into `seocho/client_bundle.py`
  - keep `seocho/client.py` focused on the public facade surface

- Accepted `ADR-0083-runtime-wiring-for-internal-orchestration-seams.md`
  - wire runtime graph reads and Cypher tool execution through `QueryProxy`
  - build the shared semantic flow through the canonical `seocho.query.AgentFactory`
  - normalize debate readiness through `AgentStateMachine`
  - defer debate-factory convergence and `LLMProxy` to later slices

- Accepted `ADR-0084-typed-query-contract-for-runtime-and-semantic-paths.md`
  - make `QueryProxy` the owner of the shared typed query row/error contract
  - keep `run_cypher()` compatibility while adding typed `query()` on the
    legacy connector surface
  - surface semantic query contract failures as diagnostics instead of silently
    flattening them into empty graph results
  - classify finance benchmark query-contract failures under an explicit query
    diagnosis code

## 2026-04-19

- Accepted `ADR-0088-beads-bootstrap-and-sandbox-workflow.md`
  - replace `bd sync` with `bd bootstrap` as the safe best-effort workspace recovery step
  - replace `bd --no-daemon` guidance with `bd --sandbox ...` for repo-local lint and worktree-safe operations
  - align active scripts and agent docs with the supported `bd 0.60` CLI

## 2026-05-03

- Accepted `ADR-0089-in-repo-docs-site-and-pages-deploy.md`
  - move the `seocho.blog` Astro/Starlight app into `website/` in the main repository
  - keep repo-root `README.md` + `docs/*` as the canonical docs source and generate selected site pages at build time
  - replace the separate-repository website sync contract with in-repo quality and GitHub Pages deploy workflows

## 2026-05-23

- Accepted `ADR-0093-layered-document-version-chunk-ingest-contract.md`
  - local SDK ingest now materializes `Document -> DocumentVersion -> Chunk -> Entity`
  - chunk embeddings are written through the local `vector_store` only after a successful graph write
  - vector rows preserve `workspace_id`, `memory_id`, `document_id`, `version_id`, and `chunk_id` so retrieval remains joinable to graph provenance

- Accepted `ADR-0094-section-layer-and-structured-local-ingest-contract.md`
  - local text ingest now materializes `Document -> DocumentVersion -> Section -> Chunk -> Entity`
  - chunk metadata and vector rows preserve `section_path` so retrieval can expand through structure before broad document context
  - local `Seocho.add_graph(...)` reuses ontology validation, layered memory shaping, and vector join metadata for caller-supplied graph payloads

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

- Accepted `ADR-0072-ontology-run-context-strategy.md`
  - define `OntologyRunContext` as the target middleware contract for SDK and runtime agent paths
  - align single-turn, multi-turn, reasoning, tool use, debate, policy, graph scope, and evidence status through one compact context envelope
  - defer Rust, Arrow, GraphAr, DataBook, vineyard, and request-time Owlready2 until Python context overhead is measured

- Accepted `ADR-0073-property-graph-lens-semantic-overlay.md`
  - preserve schemaless property graph flexibility while marking only agent-visible anchors, evidence sources, evidence paths, provenance, importance, confidence, and context metadata
  - make ontology an agent-readable semantic overlay instead of a mandatory total graph schema
  - keep the first lens implementation read-only, bounded, and Python/DozerDB-native before adding analytics or native acceleration

- Accepted `ADR-0080-internal-orchestration-seams-for-modular-monolith.md`
  - introduce `DomainEvent`, `IngestionFacade`, `QueryProxy`, `AgentFactory`, and `AgentStateMachine` as internal seams
  - keep `seocho/client.py` as the public SDK facade while moving orchestration helpers behind it
  - treat `seocho/index/*` and `seocho/query/*` as canonical engine owners, not new public APIs

- Accepted `ADR-0081-local-engine-module-behind-client-facade.md`
  - move `_LocalEngine` from `seocho/client.py` to `seocho/local_engine.py`
  - keep local-mode orchestration behind the public `Seocho` facade instead of inside it
  - enforce the boundary in basic CI and module-ownership checks

- Accepted `ADR-0082-client-remote-and-bundle-helpers.md`
  - move HTTP transport/request dispatch helper ownership to `seocho/client_remote.py`
  - move runtime-bundle import/export glue to `seocho/client_bundle.py`
  - keep `Seocho` as the stable public facade while shrinking `client.py`

## 2026-04-18

- Accepted `ADR-0085-image-backed-local-runtime-source-contract.md`
  - make the default local `extraction-service` compose path image-backed
    instead of bind-mounted from a dirty checkout
  - move live source mounts behind `docker-compose.dev.yml` and `make up-live`
  - keep port `8001` reproducible from a known build snapshot during benchmark
    and support loops
- Accepted `ADR-0086-user-first-readme-and-docs-entrypoints.md`
  - make GitHub `README.md` and `docs/README.md` lead with product value,
    first-run paths, and copy-paste snippets
  - keep architecture depth available through a clear deep-dive CTA instead of
    leading with framework comparison language
  - keep mirrored website docs home aligned with the same source-of-truth docs
    structure

## 2026-04-19

- Accepted `ADR-0087-indexing-design-specs-for-graph-model-aware-ingestion.md`
  - introduce YAML-backed indexing design specs with required ontology binding,
    explicit graph model, and explicit storage target
  - treat inquiry-cycle reasoning as a bounded anomaly-driven contract where
    abductive output stays candidate-only by default
  - install LPG-specific extraction prompt shaping in the local SDK so
    property-graph strengths are expressed during extraction rather than
    reconstructed after the fact
- Accepted `ADR-0088-beads-bootstrap-and-sandbox-workflow.md`
  - use `bd bootstrap` as the safe best-effort Beads workspace recovery step
  - use `bd --sandbox ...` for repo-local issue operations that should avoid
    auto-sync side effects
  - use `scripts/pm/bd-recover.sh` as the first-line Beads/Dolt diagnostic
    before deleting, reinstalling, or resetting `.beads`

## 2026-05-23

- Accepted `ADR-0096-sqlite-default-qualification-store-and-canonical-projection-contract.md`
  - keep observed ingest and canonical serving in separate persistence planes
  - use SQLite as the default mutable qualification store, with DuckDB as an
    optional analytics backend
  - project canonical entities/relations back into the graph store instead of
    destructively rewriting raw observed ingest

- Proposed `ADR-0095-agentic-graph-cot-query-lane-and-guardrail-contract.md`
  - keep `query_mode="graph_cot"` on the public semantic surface but route it
    toward a dedicated internal lane:
    `SemanticLayer -> QuerySupervisorAgent -> Text2CypherAgent ->
    AnswerGenerationAgent -> AnswerGuardrailAgent -> Finalize`
  - define typed handoff artifacts:
    `GraphCoTQuestionFrame`, `SupervisorDirective`,
    `QueryEvidencePacket`, `AnswerDraft`, and `GuardrailVerdict`
  - treat ontology guardrail "intuition" as a soft suspicion signal only; it
    may revise or refuse, but it may not add new facts

## 2026-05-19

- Proposed `ADR-0092-graph-cot-lpg-property-schema.md`
  - properties are an agent control surface, not descriptive metadata;
    five groups (identity, retrieval/use, reasoning, evidence, scope)
  - required node properties: `id, title, claim, agentSummary,
    semanticRole, reasoningRole, answers, useWhen, confidence,
    sourceRefs, embeddingText`; required relationship properties:
    `relationSummary, reasoningRole, confidence, sourceRefs`
  - fixed enums for `semanticRole`/`reasoningRole`/relationship types;
    promotion rule (long nested values become nodes); pipeline ensures
    fulltext + vector + property indexes per workspace

- Proposed `ADR-0091-query-enrichment-router-and-parallel-fan-out.md`
  - `QueryEnrichmentRouter` runs as a pre-stage in `Session.ask()` and
    as the canonical `augment_fn` for `GraphAgenticLoop`; one
    implementation, two callers
  - augmentation (entity/intent/topic/rewrite) feeds existing
    `RoutingPolicy.decide()`; parallel fan-out across
    Cypher/vector/fulltext/GDS via `asyncio.gather` with per-backend
    weight cutoff and timeout; short-circuits to Tier-1 NL→Cypher on
    high-confidence single-entity lookups
  - Reciprocal Rank Fusion as the deterministic baseline behind a
    pluggable `Fusion` interface

- Proposed `ADR-0090-tiered-nl2cypher-query-agent.md`
  - evolve `QueryAgent` in place with a tiered NL→Cypher policy
    (template lookup → similar past queries → schema-grounded generation
    → validate → execute → cache write-back)
  - add `cypher_template_lookup`, `similar_query_search`, and
    `schema_introspect` tools in `seocho/tools.py`; expose
    `validate_cypher` explicitly
  - add a per-workspace `NLCypherExampleStore` alongside FAISS/LanceDB in
    `seocho/store/vector.py`; tracked as subtask of `seocho-j965`
    (GraphAgenticLoop)

## 2026-04-23

- Updated FinDER query benchmark contract
  - record `support_claim_answer_mismatch` when runtime evidence claims
    `support_status=supported` but the answer still misses the reference
  - expose `support_answer_gap_count`, `support_answer_gap_rate`, and
    `diagnosis_counts` in FinDER summaries
  - document SEOCHO as an ontology-aligned modular monolith with data, control,
    ontology, runtime, and compatibility planes

## 2026-07-13

- [Accepted] ADR-20260713-sdcr-product-boundary
  - isolate graph views and route by required answer slots
  - add protected evidence filtering, conflict verification, and decision receipts
  - keep GNN, full OWL hot-path reasoning, and LLM-judge replacement out of the first product slice

## 2026-07-14

- [Accepted] ADR-0154 provenance-first extraction and evidence-conditional evaluation
  - anchor extracted figures to source tokens at write time; align facts by provenance, not names
  - ontology_role defaults to validator (SHACL/rules post-hoc + serving-time type labels), not extraction guide
  - evaluation reports grounded/contaminated/honest-abstention, never gold overlap alone

- [Accepted] ADR-0153 production-agent-harness-and-postgres-resilience
  - add scoped agent principals, bounded delegation, tool-boundary guardrails,
    and versioned harness/rubric promotion gates
  - add bounded iterative retrieval with structured unknown on budget
    exhaustion
  - add PostgreSQL workload isolation, single-flight cache, freshness-aware
    routing contracts, retry budget, query-digest blocking, and schema-change
    guardrails
  - qualify only the controls exercised by the live single-primary benchmark;
    physical replica/failover/PgBouncer/cascading replication remain separate
    deployment tests

## 2026-08-15

- [Proposed] ADR-0155 rust-dataplane-proxy-for-unified-cache-layer
  - the cache-layer data plane (Bolt relay, KV reverse index, xlat table, Arrow projection) is a new Rust component from day one (docs.rs `neo4j` crate client leg, `neo4rs` fallback)
  - the Python SDK Bolt path is NOT rewritten; gate = measured db.client server_share from PR #482
  - Python touches the data plane only at control points; canonical SDK behavior stays in src/seocho/
  - risk: second toolchain + Bolt relay protocol drift, bounded by DozerDB 5.26 LTS pin and a relay-overhead kill criterion

- [Accepted] ADR-0144 amendment — single metrics pipeline
  - route the ADR-0144 §6 counters through the ADR-0146 registry (seocho.metrics), under catalog dotted names
  - remove the tracing module's private OTel meter; one provider, one env switch, label budget enforced everywhere
  - keep seocho.tracing.record_metric as a legacy-name shim; uncataloged names are dropped
  - risk: legacy snake_case series end at the rename — none were referenced by any dashboard or rule

## 2026-08-15

- [Accepted] ADR-0156 h0-gate-verdict-working-sets-diverge
  - measured on SF1/SF10 FinBench replay: DB and KV working sets diverge with scale (top-decile Jaccard 0.226 -> 0.050); KV is an anchor-centric subset (containment 1.0)
  - per the plan's own gate: joint budget (WP3) and cross-prefetch dropped; WP4 invalidation and WP2 KV-side optimization kept; ADR-0155 data plane narrowed to invalidation+observation
  - H1 left open (share rising with scale); rerun at SF100 before pin/quantization verdict
  - caveat: read set is variable-binding based (CE exposes no page identities) — biases overlap up, so FAIL is robust

## 2026-08-15

- [Accepted] ADR-0157 agentos-surface
  - one facade (seocho.agentos.AgentOS) binds the five pillars to two interfaces: Bolt-aware governed store path, OpenAI Agents SDK (Session protocol memory, RunHooks, tool_input_guardrail)
  - tenancy pinned never trusted (model-supplied workspace params overwritten); one admission gate inside the tool; budget exhaustion structured; truncation always disclosed
  - scalability validated live: SF1/SF10 x N in {1,4,16}, 336 calls, zero bound violations at the Bolt boundary
  - remaining on epic seocho-xdp: fairness (S1), routing exposure, durable PG session backend

## 2026-08-15

- [Accepted] ADR-0158 execution-scheduling-ablations (E1/S1 measurement record)
  - E1: governed admission keeps p50 at single-session latency under 16-way contention (93.7ms vs 223.7ms light; 8.0s vs 21.8s heavy) and converts overload into structured rejections; bound held in every cell
  - S1: a 2-permit priority reserve takes high-class starvation from 94% timeouts to zero at an explicit normal-throughput price; Jain 1.0 within-class both arms
  - claim stated carefully: the layer makes the contention trade visible and configurable, not free
  - PriorityAdmission ships on AgentOS (reserved_for_high, default 0)

## 2026-08-15

- [Accepted] ADR-0159 scheduler-v2-p99 (E2/S2 measurement record)
  - probe caught estimate poisoning: global EWMA + fast-fail starved the polite high class 0/85; fixed with per-lane service EWMA — fast-fail is only as safe as its estimator
  - E2: with a correct estimator, single lane + fast-fail holds light p99 at 122ms; static lanes pay a partition tax (565ms) — lanes demoted to opt-in
  - S2: work-conserving reserve keeps interactive protection while lifting normal throughput +56~59% vs the static reserve
  - defaults: single lane + fast-fail + borrowable reserve, all off-by-default on Seocho(...)

## 2026-08-15 (interning)

- [Accepted] ADR-0160 interning-measurement (identity table = memory allocator)
  - exercises real compute_node_identity over FinBench Person/Company, SF1+SF10
  - collision: name_only 100% (SF10 569/569 homonym pairs aliased) vs composite 0% — 2,845 Person addresses lost to wrongful merges at SF10 under name-only
  - collapse: case/whitespace 100%; suffix recall 0% (honest ceiling → alias/same_as follow-up)
  - scale-invariant; feeds the allocator/interning Tier-1 claim (seocho-gzo, seocho-5r2)

## 2026-08-15 (subgraph retrieval)

- [Accepted] ADR-0161 subgraph-retrieval (boundary-1 resolution, seocho-zfe)
  - real compute_node_identity + bge over FinBench; ceiling+floor controls; scale-invariant Company SF1/SF10 + Person SF1
  - CONFIRMED: naive vector_name 50% wrong-anchor on homonyms (silent wrong subgraph); intern 0% homonym error by construction (exact/auditable)
  - REFUTED overclaim: vector_disamb ~0% everywhere on clean synthetic names — vector not structurally incapable; distinction is guaranteed-vs-empirical + cost/auditability, not capability
  - HONEST weakness: intern 100% miss on suffix variants (normalizer recall ceiling, closeable by alias/same_as)
  - design = intern-first + vector fallback (hybrid); stronger than 'we beat vector'

## 2026-08-15 (real-data interning)

- [Accepted] ADR-0162 interning-real-mdm (validation on live DozerDB golden master)
  - real cross-model duplicates (DeepSeek/gpt-oss/MiniMax x categories), MDM GoldenEntity = ground truth; 114 SourceRefs / 48 golden clusters
  - CONFIRMS synthetic: exact intern_name P=1.000 (never merges distinct golden entities) with recall ceiling 0.811; MDM's own business_key also ceilinged (0.764) -> production needed a fuzzy layer
  - semantic fallback vector_bge R=0.896/F1=0.945 recovers the miss -> validates the hybrid (seocho-6l8) on real data
  - NEW insight: name-only out-recalls name+label (0.811>0.755) because models disagree on labels -> don't over-specify identity_keys with model-contested fields
  - real missed cases: Delta Air Lines/Delta, Pfizer Inc./Pfizer, Chipotle Mexican Grill Inc./CHIPOTLE, Enphase Energy Inc./ENPHASE

## 2026-08-15 (OS I/O plane split)

- [Accepted] ADR-0163 control-data-plane-split (the OS's I/O subsystem)
  - control plane (admission/tenancy/budget/classification/observability) = Python, low QPS; data plane (Bolt round-trip + PackStream, LLM token stream) = high QPS optimization surface
  - seam = SeochoOS.execute_query (control) -> graph_store.query (data); RunHooks (control) over LLM I/O (data)
  - neo4j-bolt-rs = a DATA-PLANE driver swap beneath the gate; gated on server_share measurement (ADR-0155 discipline, no Rust on speculation)
  - design rule: a change is control-plane XOR data-plane

## 2026-08-15 (ablation A2 isolation)

- [Accepted] ADR-0164 ablation-a2-isolation (seocho-76k) — isolation leak rate OFF vs ON on live DozerDB 2-tenant graph
  - enforcement OFF leaks 21 cross-tenant rows across 5/6 attacks (wrong_node_binding worst at 9); ON leaks 0 (0/6), every attack blocked with a reason
  - properly_scoped control passes both arms (3 acme rows, 0 leak) => gate blocks attacks without over-blocking
  - first Level-2 ablation row measured; validates shipped defense-in-depth (per-workspace-DB endgame would make it structural)

## 2026-08-15 (ablation A6 server_share)

- [Accepted] ADR-0166 ablation-a6-server-share (seocho-xju) — the OS I/O plane, bolt-rs gate
  - live finbenchl10: control-plane governance ~0.06-0.07ms = negligible (<=4.1% light, 0.1% heavy); server_share 95.9-99.9%
  - OS control plane is nearly FREE (composition-overhead check passes); data plane dominates but rust-ext codec (ADR-0111) already captured the lever
  - decision: bolt-rs = not-yet, needs its own A/B (ADR-0163 discipline held); completes Level-2 A1-A6

## 2026-08-15 (ablation L1 integrated)

- [Accepted] ADR-0167 ablation-l1-integrated (seocho-41a) — OS-vs-bare on one mixed 2-tenant concurrent load, live DozerDB
  - cross-tenant leaks BARE 4800 / OS 0; truncation disclosure 0.0 / 1.0; max store concurrency 12 / 4 (admission-bounded)
  - disclosed cost: OS p99 272 vs 155ms (concurrency-bound queueing tail; benefit shows at scale, ADR-0159 optimizes it)
  - guarantees COMPOSE under load; completes ablation Level-1+Level-2; task-correctness parity (agent+judge) is the remaining axis

## 2026-08-15 (ablation A4+A5)

- [Accepted] ADR-0165 ablation-a4-a5 (resources + execution honesty)
  - A4 budget (seocho-4rb): OFF spends 32000/40 turns unbounded; ON halts turn 13 at 10400, overshoot 400 (< one turn) — structured stop
  - A5 honesty (seocho-2ay): over-cap disclosure ON=1.0 (truncated flag always) vs OFF=0.0 (silent, partial looks complete)
  - Level-2 rows A1-A5 now measured; A6=seocho-xju, L1 integrated=seocho-41a next

## 2026-08-16 (ablation L1 task axis)

- [Accepted] ADR-0168 ablation-l1-task-parity (seocho-41a) — does governance cost answer quality? MARA gpt-oss-120b agent, BARE vs OS, live finbenchl1
  - near-parity: BARE 5/5, OS 4/5; OS tokens 5.4x (18138 vs 3389 — schema-in-context + guardrail-retry)
  - the one OS miss is diagnostic: guardrail steered the agent to a schema-conformant query on an off-schema property (owner_id) -> returned 0 vs gold 5; BARE unconstrained got it
  - honest headline: governance near-free on correctness, at token cost + conformance-vs-raw trade (NOT 'free'); guardrail over-strict on aggregate LIMIT (seocho-6md); OS db-routing bug worked around (seocho-933)
  - L1 complete on both axes: dominates governance (0167) at near-parity on task (0168)

## 2026-08-16 (killer: ICL vs enforcement)

- [Accepted] ADR-0169 killer-icl-alignment (seocho-41a) — in-context specification vs enforced alignment, live finbenchl1, gpt-oss-120b + gemma-4-31B
  - conformance is an ICL dose-response (soft): none 0% / labels 0% / full 66%(gpt),16%(gemma) / full+examples 100% both, drift 0
  - full+examples (worked exact-form examples) is the lever that closes 0->100%; ontology-alone (full) insufficient esp. for weak models
  - full/hard (OS default) is worst: 2x queries (repair loop), 41-50% conform, most tokens, gpt-oss 6/6->5/6 (stuck re-emitting)
  - killer conclusion: good in-context spec beats hard enforcement; examples-first-try + enforcement-as-safety-net; repair loop is append-only multi-turn => KV prefix-reuse candidate (cuts prefill not retries/decode; examples cut retries) - seocho-40j
  - guardrail bug surfaced: result_limit_exceeded fires on aggregates + unactionable rejection msg => 6-turn flail failure (seocho-6md)
  - explains scale-up: OS looked bad on MiniMax(4/8)/gemma(5/8) because = full/hard config, not governance cost

## 2026-08-16 (OS examples validation)

- [Accepted] ADR-0170 os-examples-validation (validates #524, seocho-41a/6md) — shipped worked examples kill the repair loop
  - real Session.agent() on live finbenchl1, gpt-oss + gemma: OS(examples) 6/6 both, BARE 5/6 & 6/6; guardrail rejections = 0 (no repair loop)
  - the earlier OS underperformance (scale-up MiniMax 4/8, gemma 5/8) was the missing-examples config, not a governance cost
  - token residual is now purely the stable schema prefix (~756 tok, KV-cacheable seocho-40j); retry churn = 0

## 2026-08-16 (scale-up: adversarial + off-schema)

- [Accepted] ADR-0171 scaleup-adv-offschema (seocho-41a/5ny) — where governance wins/costs, real LLM agent
  - adversarial (prompt-injection to cross tenant): OS 0 leaks both models (gpt-oss stays scoped WITHOUT refusing = structural; gemma refuses 2/2); BARE leaks (gpt-oss 5 globex names + count, gemma count) => governance WINS safety structurally
  - off-schema (owner_id, undeclared): OS 0/2, BARE 2/2 => governance COSTS reach (ADR-0168 generalized; addressable by declaring the property)
  - full picture: in-schema parity (0170) + adversarial safety-win + off-schema reach-cost; honest headline = OS trades reach for guaranteed safety + in-schema parity

## 2026-08-16 (allocator eviction)

- [Accepted] ADR-0180 allocator-eviction (seocho-ia4) — the reclamation half of the allocator
  - gap (hadry): interning=alloc + admission=scheduling, but NO eviction/GC/lifecycle; status quo = naive fixed-LRU (id-keyed, no cost/fairness/budget)
  - CostAwareEvictionCache: GDSF (freq x recompute_cost / size) + per-tenant floor + shared boost + byte budget + thread-safe; keyed by stable content hash
  - vs naive LRU under multi-tenant skewed churn: hit-rate 35.3%->48.1%, recompute-ms avoided +36%, hot-shared retention 86.6%->99.9%
  - completes the allocator (alloc+reclaim+schedule); Memory+Resource tracks; follow-ups: wire into OntologyContextCache, extend to prefix-KV/buffers, TTL/version retirement, provenance GC
## 2026-08-16 (ontology drift barrier)

- [Accepted] ADR-0175 ontology-drift read barrier (seocho-ia4.1) — detect->enforce
  - two verified bugs: GraphProjector.project() never stamped _ontology_* (drift blind on projected data); enforce_drift_policy had zero call sites (warn-only)
  - fix (wiring, no new mechanism): projector stamps ontology_context_graph_properties; local_engine + execute_cypher run enforce_drift_policy(policy=warn|raise|block); SEOCHO_ONTOLOGY_DRIFT_POLICY
  - ablation OFF vs ON: drift detection 0%->100%; false-positive on fresh data 0%->0% (null control, no fresh-data tax); worst=breaking bump caught+blocked, best=no-bump quiet
  - 0 regressions; +4 tests; first shipped step of ontology-lifecycle OS (ia4); Trust/Safety+Long-Horizon tracks
## 2026-08-16 (ontology freshness policy)

- [Accepted] ADR-0176 bounded-staleness freshness policy (seocho-ia4.6) — strict, but not stale
  - ia4.1 barrier is binary (mismatch->block) = unconditional strict = over-refuses; warn = under-refuses
  - evaluate_freshness: staleness = version_distance x drift_relevance, gated by coverage/age; serve/repair/refuse
  - refusal-ROC ablation: always_warn (under 100/over 0), always_block (under 0/over 100), freshness b=H (0/0) => dominates both corners; bound sweep = graceful ROC frontier
  - honest: synthetic mechanism-frontier demo (separates the two error types fixed policies cannot); live payoff needs ia4.2 classifier (relevance/horizon) + ia4.3 version chain (distance)
  - 7 tests; standalone module; Long-Horizon + Trust/Safety tracks

## 2026-08-16 (compatibility classifier + live freshness)

- [Accepted] ADR-0177 typed compatibility classifier -> live freshness signals (seocho-ia4.2 + ia4.6)
  - classify_ontology_change: BACKWARD/FORWARD/BREAKING per change atom (structural, no DL); fixes diff_ontologies false-major (add-optional flagged breaking); breaking_labels + breaking_properties + semver_distance
  - live refusal-ROC (real v1->v2 diff, property-level ground truth): always_warn 100/0, always_block 0/100, fresh_OLD(false-major) 0/83, fresh_label 0/50, fresh_prop 0/0 => freshness dominates corners; over-refusal shrinks with signal fidelity (OLD->label->prop), 0 under throughout
  - non-tautological (ground truth property-level, signals coarser); fully-live (real data answers) = pending e2e
  - 8 tests; promotes ADR-0176 to real signals
## 2026-08-16 (axiom induction + deduction)

- [Accepted] ADR-0178 inductive axiom mining + deductive entailment, A/B vs SHACL-only (seocho-ia4.8/9/10)
  - axioms.py: mine_axioms (functional/inverse-functional/disjoint/subclass/AMIE-lite rules w/ support+confidence) + approve() gate + materialize_entailments (subclass closure + rule edges marked _entailed; functional/disjoint contradiction detection); structural, owlready2 stays offline
  - resolves 'axiom extraction cumbersome' (mined not authored -> approval gate) + 'SHACL shapes human-in-loop' (shapes induced)
  - A/B (offline fixture): SHACL-only 0 axioms / 0 contradictions / 0 entailed vs induced+deduced 12 axioms / 2 contradictions caught (functional+disjoint, which SHACL can't see) / 1 entailed edge; approval burden 12 of 15
  - honest: mechanism measured offline; ANSWER-QUALITY delta = pending live e2e (never run) which gates DL-as-shape ia4.7
  - 6 tests; insertion point pipeline.py:1002; complements rules.py
## 2026-08-16 (cold-start schema bootstrap)

- [Accepted] ADR-0179 cold-start schema bootstrap — upper-ontology-anchored open extraction (seocho-ia4)
  - principle (hadry): domain-driven interface = design against an ABSTRACT upper ontology, concrete types emerge anchored under it (no dataset-quirk hyperfixation)
  - upper.py: ~11-category foundational ontology + abstract relations, small/soft (avoids firewall recall hit); render_upper_frame()
  - induce.py: induce_ontology_from_graph (concrete types -> NodeDef broader=[upper], relationships from majority endpoints) + optional mined axioms; induction_report drift diagnostics
  - Keet triad: cold-start = abduction (hypothesize types) -> induction (mine schema/axioms) -> deduction; 1 pass + growing soft frame, no forced re-extraction
  - core landed+tested; WIP = live bootstrap extraction mode + cold-start A/B (drift/axiom-support/recall vs pure-open) on instance-diverse corpus

## 2026-08-16 (cold-start extraction A/B)

- [Accepted] ADR-0181 cold-start extraction A/B pure-open vs upper-anchored (seocho-ia4.11)
  - live MARA extraction, FinDER 10 docs; only variable = extraction context
  - RECALL no penalty (bootstrap 86n/92r vs pure-open 69/69, coverage 8/9 vs 9/9) => small abstract frame is recall-safe (firewall re-test confirmed)
  - structure/axiom-support: bootstrap wins (hierarchical types 36/36 vs 0, axioms 20 vs 13)
  - drift INCONCLUSIVE: string-norm metric too weak for semantic synonyms; bootstrap increased granularity; true control = grouping under ~11 upper cats not fewer types; embedding-cluster metric = follow-up
  - decision: wiring bootstrap mode into engine warranted (recall fear disproven)
## 2026-08-16 (indexing parallelism)

- [Accepted] ADR-0182 indexing parallelism — concurrent extraction + shared intern table (seocho-ia4)
  - step 1: concurrent_map pre-fetches per-chunk LLM extraction (I/O-bound -> thread pool, order-preserving, opt-in SEOCHO_EXTRACTION_CONCURRENCY); 151 index tests pass identically off AND on
  - step 2 profile: extraction near-linear (8w=5.99x, 12w=11.94x); interning 1.19M ops/s (~0.5ms/doc, negligible)
  - step 3: SharedInternTable = thread-safe workspace-scoped sharded intern table (shared-memory core); 16 threads racing one entity converge to one canonical id (no fragmentation)
  - step 4: Rust intern table NOT warranted now (data: extraction I/O-bound, interning 1.2M ops/s); trigger documented; measure-first
  - +7 tests

## 2026-08-16 (cross-model shared intern table)

- [Accepted] ADR-0183 cross-model + cross-session shared intern table (seocho-ia4)
  - SharedInternTable.persist/load = cross-session canonical namespace (heap outlives process)
  - experiment: same ontology + FinDER 10 docs, ONE shared table, 3 model families (MiniMax-M2.7/gpt-oss-120b/gemma-4-31B-it via MARA)
  - result: 23 canonical entities; ALL-3 agreement 15 (65%), >=2 17 (74%), unique-to-one 6; collapse 35 cross-model+doc folds
  - => ontology = shared type system + address space across models = OS memory claim embodied (heterogeneous clients, one governed heap)
  - honest limit: 26% + name variants (berkshire hathaway vs ... inc.) = boundary-1 recall ceiling now cross-model; fuzzy fallback = follow-up

## 2026-08-16 (publish compatibility gate)

- [Accepted] ADR-0184 publish-time compatibility gate (seocho-ia4.2)
  - publish_gate.check_publish_compatibility: BACKWARD(default)/FORWARD/FULL/NONE; refuse incompatible publish; first version always ok
  - OntologySnapshotStore.publish() = gated save (allow_breaking bypass); plain save() untouched
  - derive_drift_policy ties verdict to ia4.1 read barrier (BREAKING/FORWARD->block); PublishCompatibilityError carries report
  - turns silent-breaking publishes into explicit blocked-by-default; +4 tests; completes ia4.2

## 2026-08-16 (pin-aware eviction)

- [Accepted] ADR-0185 pin-aware eviction — safe-reclamation gate (light) (seocho-ia4.4)
  - eviction cache ranked value but had NO safety gate -> could evict an in-flight entry (use-after-evict bug)
  - add pin/unpin/pinned() refcount; _evict_to_budget skips pinned entries; stats.pinned
  - light gate (RCU-free) closes the bug now; full epoch-based version reclamation waits on ia4.3
  - +2 tests

## 2026-08-16 (memory-manager demo + tombstone migration)

- [Accepted] ADR-0186 memory-manager demonstration + tombstone migration (seocho-ia4.4/ia4.5)
  - Part A (demo, hadry scenario): demo_memory_manager.py runs ref-count->lookup->fill->pressure->pin->churn->unpin; 4 invariants ALL hold (pinned-in-use never evicted, hot-shared retained, cold reclaimed on unpin, evicts under pressure); 64 evictions, hit-rate 49%
  - Part B (ia4.5): migration_plan(tombstone=True default) = SET _ontology_tombstoned_at instead of DETACH DELETE; removed prop kept+_deprecated_ not dropped; data_loss flag per stmt; tombstone=False = legacy destructive
  - non-destructive migration by default (VACUUM discipline); epoch-gated vacuum+RELABEL/BACKFILL+scavenger = ia4.3/4.5 follow-up; +3 tests

## 2026-08-16 (text2cypher intern grounding)

- [Accepted] ADR-0187 text2cypher grounding via shared intern table + competency questions (seocho-ia4)
  - query/intern_grounding.py: resolve_mentions (request mentions -> canonical ids via SharedInternTable; unresolved = can't-find-entity signal for fuzzy routing) + rank_competency_questions (tf-idf cosine intent) + ground_request
  - attacks the Cypher-gen agent's hardest moment (entity resolution + intent); model-agnostic via cross-model shared namespace (ADR-0183)
  - honest: exact-name resolution (variants surface as unresolved = boundary-1 ceiling), tf-idf baseline (bge next); +4 tests
  - follow-up: wire into live cypher-gen prompt + repair loop (PR #542 merged), vector fallback for unresolved

## 2026-08-16 (terminology: soft-delete)

- [Clarification] rename ontology-migration "tombstone" -> "soft-delete" (hadry: term unclear)
  - migration_plan(soft_delete=True) [was tombstone]; graph props _ontology_soft_deleted_at / _soft_delete_reason [were _ontology_tombstoned_at / _tombstone_reason]; new, no production data, safe rename
  - audit of this session's OS-lifecycle modules: tombstone was the only genuinely-ambiguous term; the rest (GDSF, intern/interning, tenant_floor, shared_boost, anchor, staleness, AMIE-lite, epoch/watermark/fencing[design]) are standard CS/DL/schema-registry terms already explained in module docstrings — no rename needed

## Template

Use this block for new entries:

```md
## YYYY-MM-DD

- [Status] ADR-XXXX short-title
  - key decision 1
  - key decision 2
  - risk/tradeoff note
```
## 2026-07-12

- [Proposed] ADR-0149 Arrow and Parquet projection contract
  - Arrow IPC for deterministic projection batches and Parquet for replay/audit
  - capability-gated APOC Extended acceleration with typed Bolt fallback
  - never advance projection watermarks on artifact creation alone

- [Proposed] ADR-0148 cache-aware SEOCHO Prompt Package
  - define stability and tenant cache scope per prompt section
  - render by endpoint capabilities for hosted APIs, gateways, vLLM, and SGLang
  - keep prompt bodies out of receipts and require measured cold/warm validation
- [Accepted] ADR-0172 remove-opik-tracing-backend — the SDK's own 97-instrument
  metric surface covers all four golden signals, so a second, data-exporting
  path is cost without benefit; tracing contract becomes `none|console|jsonl|otlp`
- [Accepted] ADR-0173 ontology-subpackage (seocho-di8) — 16 flat `ontology*.py`
  modules (7,872 LOC) become `src/seocho/ontology/`; lazy `__init__` because the
  core/serialization cycles predate the move, `sys.modules` aliases so
  `monkeypatch` still reaches the canonical module; zero public API change
- [Accepted] ADR-0174 client-namespaces (seocho-6yf) — `sc.index` / `sc.governance`
  / `sc.platform` / `sc.sessions` group 54 of 80 facade methods; `AsyncSeocho`'s
  25 missing methods are generated rather than hand-written; additive, nothing
  deprecated yet
