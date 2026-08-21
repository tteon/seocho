# Changelog

All notable changes to this project are documented here. Versioning follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **`SeochoCredentialError` on missing LLM credentials.** Constructing an LLM
  backend (including via `Seocho.local(...)`) with no API key now raises a
  SEOCHO-native error naming the actual provider and its environment
  variable(s) (e.g. `MARA_API_KEY`), instead of the upstream `openai` client's
  `OPENAI_API_KEY`-branded error. Keyless local gateways keep working via the
  documented `api_key="EMPTY"` sentinel (vLLM applies it automatically).

## [0.6.0] — 2026-08-18

Minor release covering everything since the last published version (0.4.1,
2026-05-17). The 0.5.0 release below was prepared on 2026-06-05 but never
uploaded to PyPI; this release supersedes it and includes its
semantic-layer/arbiter and ontology-engineering content. Headline: the SEOCHO
operating layer (AgentOS), the structured query engine, identity interning as
a governed shared-memory layer, provenance + layered security, and OpenAI
Agents SDK orchestration coupling. Additive except where noted under
**Changed/Removed**.

### Added — operating layer (AgentOS)
- **`seocho.agentos.AgentOS`** — one facade binding admission control, pinned
  tenancy (model-supplied workspace params are always overwritten), token
  budgets with structured exhaustion, and always-disclosed truncation, exposed
  over the governed store path and the OpenAI Agents SDK (`Session` protocol
  memory, `RunHooks`, `tool_input_guardrail`) (ADR-0157).
- **Scheduler** — light/heavy lanes with per-lane service EWMA, fast-fail,
  work-conserving priority reserve; per-tenant scheduler instances plus
  point-lookup light-lane routing. All off by default on `Seocho(...)`
  (ADR-0158/0159/0207).
- **Measured guarantees** (ablations ADR-0164..0168): cross-tenant leaks
  21 → 0 under attack; budget overshoot under one turn; truncation disclosure
  0 → 1.0; control-plane overhead ≤ 4.1% of request time; task-correctness
  near-parity under governance (disclosed cost: ~5x tokens on the agent path).

### Added — structured query engine
- **`Seocho.ask(..., engine="structured")`** — deterministic pipeline:
  pinned-schema resolve → ontology-grounded text2cypher (declared identifiers
  only, fully parameterized, workspace-scoped on every node) → guardrail →
  governed execute → synthesizer. Honest abstain reasons distinguish
  `structured_no_evidence` from `structured_guardrail_rejected`
  (ADR-0202/0205/0208).
- **Repair loop** — guardrail rejections feed back into generation with a
  bounded retry budget before abstaining (ADR-0209).
- **Per-request run-context spine** — concurrency-safe ContextVar context with
  RCU ontology version pinning; readers never see a mid-request schema swap
  (ADR-0200/0201, RCU B1–B3).

### Added — identity / shared memory
- **`SharedInternTable`** read-side canonical resolver (name-alias index,
  homonyms surface candidates instead of guessing) and opt-in cross-source
  convergence via `NodeDef.cross_source_unique` (ADR-0203/0204).
- **Workspace-scoped graph MERGE** — nodes key on `(id, _workspace_id)` and
  relationship endpoints match within the workspace, so two tenants' identical
  canonical id never share a physical node (ADR-0206).
- Pin-aware eviction, ontology freshness policy with read repair, drift
  barrier, and offline axiom induction (ADR-0186, 0193–0196).

### Added — provenance + layered security
- **`seocho.provenance` / `seocho.provenance_store`** — content-addressed fact
  ids tying Postgres ground-truth rows to graph nodes, value-free PROV-O
  bundles, RLS-backed governed projection (ADR-0211).
- **`seocho.security_levels`** — dataset/row/cell/sub-cell security over a
  public<internal<restricted<secret lattice, default-deny, with a redaction
  audit trail; sub-cell array-element filtering is new capability (ADR-0212).

### Added — Agents SDK orchestration
- Ontology guardrail wired onto factory-built agents; **controlled query
  agent** (single deterministic `answer_from_graph` tool) with Supervisor
  routing to it by default — orchestration is consumed from the Agents SDK,
  deterministic bodies stay SEOCHO's (ADR-0215/0216/0217).

### Added — client ergonomics + integration surface
- Client namespaces **`sc.index` / `sc.governance` / `sc.platform` /
  `sc.sessions`** grouping the facade; `AsyncSeocho` parity generated
  (ADR-0174).
- `seocho.ontology` is now a subpackage with an unchanged public API
  (ADR-0173).
- Read-only **connector materialization layer** (Notion, Slack, DataHub,
  PostgreSQL, Neo4j/DozerDB + LangChain/LlamaIndex converters) writing
  `seocho.connector_record.v1` JSONL (ADR-0150).

### Changed / Removed
- **Tracing backend contract is now `none | console | jsonl | otlp`; the Opik
  backend was removed** (ADR-0172). Metrics consolidate into the
  `seocho.metrics` registry (single provider, bounded labels).
- New extras: `otel`, `postgres`, `memory-bench`.

### Operations
- Added release and Discord community operating criteria in
  `docs/RELEASE_AND_COMMUNITY_OPERATIONS.md`.
- Added a GitHub release checklist issue template to capture release gates,
  release notes, and the `#seocho` announcement draft before publishing.

### Known gaps
- Release validation = basic CI + clean-venv wheel build, import smoke of all
  headline modules, `seocho --help`, and `Seocho.local(...)` construction
  against embedded LadybugDB. Live LLM/graph end-to-end and answer quality are
  tracked separately (ADR-0214/0218 caveats apply).
- `Seocho.local(...)` without an `api_key` fails at construction with an
  upstream OpenAI-branded credential error; a SEOCHO-native message is a
  follow-up.
- `runtime/` (agent server, policy) remains git/Docker-distributed by design;
  the wheel ships the SDK only.

## [0.5.0] — 2026-06-04 (not published to PyPI; superseded by 0.6.0)

Minor release shipping the ontology-as-semantic-layer + arbiter retrieval path
(ADR-0103) and a new ontology-engineering layer. The previously published 0.4.1
predated all of this. Additive and env-gated; no breaking API changes.

### Added — semantic layer + arbiter (ADR-0103)
- **`seocho.semantic_layer`** — closed `MetricConcept` vocabulary (the LLM selects,
  never invents), CIK entity identity, typed periods, and reified `Observation`
  nodes with a deterministic `obs_id` SHA1 key (idempotent `MERGE` across chunks).
- **`seocho.query.arbiter`** — neutral *measure → routing hint* (`STRUCTURED` /
  `NARRATIVE` / `CLARIFY` / `FAIL`); turns a silent empty structured result into an
  explicit, observable route (`ArbiterHint.to_span()`).
- **`seocho.query.semantic_query.semantic_answer`** — decompose → arbitrate →
  compile → execute → format; MARA-first, no fallback masking.
- **`seocho.index.observation_writer`** — transform extracted nodes/rels into
  reified `(Company, Observation)` records.

### Added — ontology-engineering layer (GRL KGC-2026 methodology)
- **Competency questions** — `ontology_governance.competency_question_report()`
  (wires the previously-dead `competency_question_coverage`) for a per-arm
  expressible / schema-impossible verdict; authored CQ set under `examples/`.
- **Conformance + fix-and-resync** — `ontology_governance.conformance_score()`
  (scalar + hard gates) and **`seocho.ontology_resync.resync_ontology()`**
  (regenerate SHACL/JSON-LD + re-validate + score + diff in one offline flow).
- **Adversarial critique** — `seocho.index.extraction_critique` (env-gated
  `SEOCHO_ONTOLOGY_CRITIQUE`, recall/precision diagnostic, never auto-applied).
- `to_shacl()` now emits plain-English `sh:message`; `lint_ontology()` flags
  dangling relationship endpoints.

### Packaging
- Add `Programming Language :: Python :: 3.10` classifier (matches
  `requires-python>=3.10`).

## [0.4.1] — 2026-05-16

### Added
- **`seocho.ontology.Ontology.from_ttl(path)`** — load an ontology from an
  OWL/SKOS Turtle file. Reads ``owl:Class``, ``owl:ObjectProperty`` (with
  ``rdfs:domain`` / ``rdfs:range``), and descriptions from
  ``rdfs:label`` / ``rdfs:comment`` / ``skos:definition``. Requires
  ``rdflib`` (``pip install rdflib``). Closes the in-curriculum gap where
  notebooks expected ``Ontology.from_ttl`` but only ``from_yaml`` /
  ``from_jsonld`` / ``from_dict`` were available.

## [0.4.0] — 2026-05-16

Minor release that consolidates the engineering-improvement candidates
surfaced while building the `examples/teaching/` curriculum (8 bd tickets
closed). All additions are new top-level submodules; nothing in 0.3.x is
removed or renamed, so 0.3.x consumers upgrade without code changes.

### Added

- **`seocho.store.llm.LLMBackend.chat(text, *, system=None, ...)`** — single-shot
  convenience for notebooks / REPL. Production callers should keep using
  `.complete(system=..., user=...)`. Closes [`seocho-9s80`].
- **`seocho.index.sanity`** — read-only temporal sanity checks for indexed
  graphs. `run_temporal_checks()` returns a `TemporalReport` with 5 metrics:
  future-dated provenance, inverted temporal ranges, orphan extractions,
  stale entities, non-monotonic source versions. `assert_clean()` raises
  `TemporalAnomalyError` on any violation. Closes [`seocho-le4c`].
- **`seocho.index.metadata`** — canonical property-name constants for
  `(:Source)`, `(:Chunk)`, ontology-class entities, `[:MENTIONS]` and
  `[:RELATED_TO]`. Plus `RunContext` + `provenance_stamp()` helpers for
  callbacks that want to write standardized extraction provenance.
  Closes [`seocho-hpml`] (MVP — full IndexingPipeline integration pending).
- **`seocho.eval.benchmarks.finder`** — HuggingFace `Linq-AI-Research/FinDER`
  loader with `load() / by_category / sample_random / sample_per_category /
  category_distribution`. Schema is normalized to notebook-friendly fields
  (`id, question, document_text, answer, category, reasoning_required,
  type, references`). Cache directory honours `$SEOCHO_DATASET_CACHE_DIR`.
  Closes [`seocho-ci24`].
- **`seocho.gds`** — `gds_session()` context manager + `MetricSpec` enum +
  `GDSEstimate.fits()` heap-fraction guard + `.louvain()` that auto-writes a
  `GDSRunMeta` node. Projection auto-dropped on `__exit__` even on
  exception. Closes [`seocho-xuof`].
- **`seocho.query.guards`** — 12-pattern Cypher validator for free-form LLM
  output. `validate_cypher()` returns `list[CypherIssue]` with severities
  `block | warn | info` covering label hallucination, property typo,
  missing LIMIT, destructive ops, unbounded paths, cartesian products,
  missing DISTINCT, wrong relationship direction, temporal-ignorant
  queries. Coexists with the existing constrained-plan validator at
  `seocho.query.cypher_validator`. Closes [`seocho-ixsk`].
- **`seocho.routing`** — `RoutingPolicy.decide()` + `RoutingDecision`
  declarative routing surface, with confidence thresholds, adaptive
  context-window budget, exponential `staleness_penalty()`, and a refusal
  decision tree. `RoutingDecision.to_metadata()` emits a stable
  Opik-friendly dict. Closes [`seocho-mcg1`].
- **`seocho.debate`** — `DebatePolicy`, `convergence_curve()` (citation
  Jaccard), `should_stop()` with 5 early-stop criteria, intent →
  participants heuristic, and 5 anti-pattern detectors (echo chamber,
  sycophancy, citation drift, context drop). Closes [`seocho-vij5`]
  (orchestrator bridge to `extraction.debate.DebateOrchestrator` deferred
  to a follow-up).

### Curriculum
- New `examples/teaching/` curriculum with 5 chapter notebooks + Reveal.js slide
  decks + chapter-by-chapter depth appendices (property design, GDS
  engineering, Cypher failure taxonomy, routing decision design, debate
  convergence). All chapters demonstrate the 4-provider (OpenAI / Kimi /
  DeepSeek / Grok) comparison pattern and Opik per-member project routing.

### Notes
- `seocho.index.metadata` MVP only documents the schema and ships
  helpers; the `IndexingPipeline` write path will adopt the helpers in a
  follow-up so existing graphs are not affected by this release.
- `seocho.debate` currently exposes the telemetry primitives without
  wrapping `extraction.debate.DebateOrchestrator` — the orchestrator
  bridge will land in 0.4.x once the SDK orchestration surface
  stabilises.

[`seocho-9s80`]: https://github.com/tteon/seocho/issues
[`seocho-le4c`]: https://github.com/tteon/seocho/issues
[`seocho-ci24`]: https://github.com/tteon/seocho/issues
[`seocho-xuof`]: https://github.com/tteon/seocho/issues
[`seocho-ixsk`]: https://github.com/tteon/seocho/issues
[`seocho-mcg1`]: https://github.com/tteon/seocho/issues
[`seocho-vij5`]: https://github.com/tteon/seocho/issues
[`seocho-hpml`]: https://github.com/tteon/seocho/issues


## [0.3.2] — prior release
Earlier history is tracked in git commits; see `git log` and the
`docs/decisions/` ADRs for context up to 0.3.2.
