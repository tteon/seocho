# ADR-0092: Graph-CoT-Oriented LPG Property Schema for Indexing

Date: 2026-05-19
Status: Proposed

## Context

Today's indexing pipeline (`seocho/index/pipeline.py:317`) writes minimal
properties on extracted nodes — essentially `{name: ...}` on `Entity`
labels. The downstream Graph-CoT agents (the router in ADR-0091, the
`QueryAgent` tiered NL→Cypher in ADR-0090, and the GraphAgenticLoop in
`seocho-j965`) iteratively decide which node or neighbor to visit next.
A property surface that only describes "what this node is" gives those
agents nothing to reason with at decision time.

Graph-CoT-style reasoning treats traversal as a sequence of decisions:
*given current evidence, which neighbor advances the answer?* For that to
work, properties must function as a **control surface** the agent reads
to decide its next action — not as descriptive metadata.

ADR-0073 (property graph lens semantic overlay) already established the
agent-readable overlay principle but stopped at the structural level
(workspace_id, ontology_hash, evidence anchors). ADR-0087 (indexing
design specs) made graph-model awareness mandatory at the pipeline
boundary but did not prescribe per-node property semantics. This ADR
fills that gap.

Pairs with ADR-0091 (the router consumes these properties for entity
candidates, topic tagging, and embeddingText-based similarity) and
ADR-0090 (schema-grounded Cypher generation reads these properties via
`schema_introspect`). Subtask of `seocho-j965`. ADR-0092 must land
before ADR-0091's router can do meaningful augmentation, so the bd
ordering is 0092 → 0091 → 0090.

## Decision

Adopt a Graph-CoT-oriented property schema. Properties are grouped into
five concerns; each property either has a fixed enum or a typed shape.
The indexing pipeline emits these properties during extraction; the
agent reads them at retrieval and traversal time.

### Property groups

**1. Identity / Semantic** — what is this node
- `id` — stable globally-unique id (e.g. `claim:graphcot:iterative_traversal`)
- `title` — short human-readable name
- `claim` — core proposition the node carries; usable directly as context
- `semanticRole` — fixed enum (see below)
- `domainScope` — list of domain tags this node applies to
- `tags` — free-form supplementary tags

**2. Retrieval / Use** — when to read this node
- `answers` — list of question patterns this node can answer
- `useWhen` — list of conditions under which the agent should select this
  node during retrieval or traversal
- `doNotUseWhen` — list of conditions that disqualify this node
- `embeddingText` — short composed string used for vector search;
  default composition: `title + claim + agentSummary + answers + useWhen`

**3. Reasoning** — how to use this node in chain-of-thought
- `reasoningRole` — fixed enum (see below)
- `preferredNextRelations` — list of relationship types the agent should
  prefer when traversing onward from this node
- `graphCotUtility` — composite priority score [0.0, 1.0] for ranking
  during traversal
- `importance` — domain importance [0.0, 1.0]

**4. Evidence / Provenance** — how much to trust this node
- `confidence` — confidence in the claim itself [0.0, 1.0]
- `extractionConfidence` — confidence in the extraction process [0.0, 1.0]
- `sourceRefs` — list of source ids
- `evidenceText` — short justification quote; long evidence is promoted
  to a separate `(:EvidenceChunk)` node (see promotion rule)
- `createdAt`, `updatedAt` — timestamps

**5. Scope / Constraint** — where and when this node is valid
- `validFrom`, `validTo` — temporal validity range
- `temporalScope` — coarse bucket (`current`, `historical`, `forecast`)
- `jurisdiction`, `audience` — applicability constraints
- `assumptions` — list of short assumption strings; promote to
  `(:Assumption)` nodes when each needs verification (see promotion rule)

### Required vs recommended per node

**Required** (node creation must populate these):
`id, title, claim, agentSummary, semanticRole, reasoningRole, answers,
useWhen, confidence, sourceRefs, embeddingText`

**Recommended** (populated when extraction confidence is high enough):
`doNotUseWhen, domainScope, validFrom, validTo, importance,
graphCotUtility, preferredNextRelations, extractionConfidence`

### Relationships carry properties too

Relationship types are the canonical agentic vocabulary; the indexing
pipeline emits these only (no free-form types):

`SUPPORTS, CONTRADICTS, REQUIRES, CAUSES, IMPLEMENTS, EXAMPLE_OF,
PART_OF, ALTERNATIVE_TO, EVIDENCED_BY, SUPPORTED_BY, MENTIONS`

**Required** on every relationship:
`relationSummary, reasoningRole, confidence, sourceRefs` (or
`evidenceRef`)

**Recommended**:
`useWhen, condition, traversalCost, polarity`

### Fixed enums

`semanticRole` ∈ `{concept, claim, definition, method, example, risk,
metric, source, decision, evidence, constraint}`

`reasoningRole` ∈ `{premise, bridge, constraint, evidence,
counterEvidence, hypothesis, answerCandidate, nextStepHint, strategy}`

Free-text values are rejected at extraction validation time so the agent
can rely on stable comparisons (e.g. "prefer nodes where
`reasoningRole = bridge`").

### Promotion rule

When a candidate property would carry rich nested structure or needs
independent traversal, promote it to a node and link via a relationship:

- multi-field source → `(:Source)` or `(:EvidenceChunk)` node connected
  by `SUPPORTED_BY` / `EVIDENCED_BY`
- multiple verifiable assumptions → `(:Assumption)` nodes connected by
  `REQUIRES`
- a property whose value reaches the configured `promote_threshold`
  (default: list length > 5 or string length > 512) → separate node

Rationale: LPG property values are limited to scalars and homogeneous
lists in most backends (DozerDB/Neo4j); nested JSON breaks query
ergonomics and indexes. The promotion rule keeps properties query-friendly.

### Indexes the pipeline creates

After write, the indexing pipeline ensures three index families exist
per workspace (lazy, idempotent):

- **Fulltext** over `name + aliases + title + claim` for all labels
  emitted by extraction. Uses `extraction/fulltext_index.py:73`
  (`ensure_index`). Powers ADR-0091's entity augmentation channel.
- **Vector** over `embeddingText` per workspace via
  `seocho/store/vector.py`. Powers ADR-0091's semantic augmentation
  channel and parallel vector backend.
- **Property** indexes on `semanticRole, reasoningRole, domainScope,
  validFrom, validTo` for fast filter-during-traversal. Cypher
  constraints emitted as `CREATE INDEX ... ON ...`.

## Consequences

Positive:

- agents have a real control surface; `reasoningRole = bridge` and
  `preferredNextRelations` make Graph-CoT traversal decisions reproducible
- fixed enums make agent prompts stable across extraction batches; rules
  like "prefer `reasoningRole = evidence` when answering factual
  questions" become enforceable
- `embeddingText` is composed at index time, so the vector channel reads
  a single high-quality field instead of arbitrary text concatenations
  at query time
- `useWhen` / `doNotUseWhen` give the router and the agent a structured
  way to express applicability conditions without re-deriving them per
  question
- promotion rule keeps the graph LPG-clean — no nested JSON properties,
  no overloaded fields
- temporal scope (`validFrom`, `validTo`) eliminates a class of
  hallucinations where the agent surfaces outdated facts

Tradeoffs:

- extraction cost rises: each entity/claim now needs additional LLM
  passes (or multi-task prompts) to populate `answers`, `useWhen`,
  `reasoningRole`, `confidence`. Mitigated by batching and by promoting
  cheap fields (`semanticRole`, `domainScope`) to deterministic rules
- storage grows materially per node; expected order-of-magnitude
  increase in property bytes per workspace. Acceptable for LPG paths;
  watch index size in DozerDB
- agent prompts must be updated to reference the enum vocabularies; old
  prompts that assumed only `{name, label}` need migration
- migration: existing graphs lack these properties. A backfill pass over
  prior workspaces is required (best-effort, opt-in per workspace) before
  the router can rely on the new fields
- ADR-0091 router is downstream-blocked on at least the **required**
  property set landing; recommended fields can stream in later

Open questions (deferred):

- exact composition rule for `embeddingText` per `semanticRole` — fixed
  template or per-role override?
- whether `graphCotUtility` is computed at index time or derived from
  centrality/PageRank in a second batch pass
- backfill policy for legacy workspaces (no-op vs synthesize-from-name)

## Implementation Notes

- touch points:
  - `seocho/index/pipeline.py:317` — extend node/edge construction to
    emit the required property set; route through a `PropertyShaper`
    that enforces enums and the promotion rule
  - new module: `seocho/index/property_shaper.py` — owns the schema,
    validates enums, composes `embeddingText`, decides promotions
  - `seocho/index/extraction_engine.py` — prompt updates so the LLM
    extraction step returns `answers, useWhen, reasoningRole,
    semanticRole, confidence, agentSummary` per node
  - `extraction/fulltext_index.py:73` — invoked from pipeline end to
    ensure fulltext indexes over `name + aliases + title + claim`
  - `seocho/store/vector.py` — new write path for `embeddingText` index
    population per workspace
  - `seocho/store/graph.py:543` — `get_schema()` consumers (ADR-0090's
    `schema_introspect` tool) automatically see the new property keys
  - `docs/GRAPH_MODEL_STRATEGY.md` — document the schema as the canonical
    LPG contract
- safety skills to invoke: `refactor-safety` (pipeline + extraction
  prompt changes), `workspace-id-audit` (every new index call must scope
  to `workspace_id`), `cypher-safety` (index creation Cypher must validate
  identifiers via `validate_identifiers` in `fulltext_index.py:19`),
  `owlready-boundary` (`semanticRole`/`domainScope` derivation from
  ontology context must use the pre-computed `OntologyRunContext`, not
  request-time owlready2 reasoning)
- aligns with CLAUDE.md §6.1 (workspace propagation through pipeline),
  §6.3 (owlready offline only), §7 (centralized config, type hints), §8
  (Cypher safety on index creation), §10 (frontend-driven upload flow
  produces these properties at extraction time), §14 (philosophy:
  heterogeneous-source extraction produces ontology-governed semantics)
- depends on: nothing — this is foundational
- enables: ADR-0091 (router augmentation), ADR-0090 (richer Cypher
  generation grounding)
- parent tracking: `seocho-j965` (GraphAgenticLoop), subtask peer to
  ADR-0090 and ADR-0091. Recommended bd ordering: 0092 → 0091 → 0090.
