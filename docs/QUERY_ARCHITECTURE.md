# Query Architecture

This page explains how SEOCHO turns a question into graph-grounded evidence and
a supported answer. Read `docs/ARCHITECTURE.md` first if you only need the
system overview.

## Query In One Page

The query plane owns four decisions:

| Decision | Question | Main owner |
|---|---|---|
| intent | what does the user need answered? | `src/seocho/query/intent.py` |
| retrieval | which graph facts are relevant and answerable? | `src/seocho/query/` |
| execution | which query path is allowed and safe? | `src/seocho/query/cypher_validator.py` |
| answer | what can be said with the evidence? | semantic answer contracts |

The query layer should produce evidence, not just a fluent answer.

## End-To-End Data Flow

```text
external data
  -> ontology-shaped extraction
  -> entity linking and deduplication
  -> rule assessment
  -> graph write with provenance
  -> user question
  -> query intent
  -> graph evidence
  -> supported answer
```

Query behavior depends on the same ontology context used during indexing. If
the query loses that context, the answer can become fluent but no longer
auditable.

## Query-Time Semantic Flow

The default semantic path is built for hard entity disambiguation:

```text
user question
  -> semantic layer
     - extract candidate entities
     - search graph fulltext index
     - apply contains-based fallback
     - use ontology labels and aliases when available
     - deduplicate candidates
  -> route to LPG, RDF, or hybrid path
  -> answer generation
  -> support assessment
```

This path exists because user wording rarely matches graph labels exactly.
Fulltext-first lookup improves recall, while semantic deduplication reduces
wrong-node selection before Cypher execution.

## Canonical Ownership

| Layer | Owns | Main files |
|---|---|---|
| query engine | planning, execution, answer shaping | `src/seocho/query/*` |
| phase A | intent, support, strategy, Cypher validation | `intent.py`, `strategy_chooser.py`, validators |
| phase B | constraint slices and run metadata | `constraints.py`, `run_registry.py` |
| phase C | entity resolution, routing, LPG/RDF specialists | `semantic_agents.py` |
| phase D | shared semantic orchestration | `semantic_flow.py` |
| compatibility | runtime injection and legacy aliases | `extraction/*` |

Rule of thumb: add new query behavior under `src/seocho/query/`. Keep
`extraction/*` focused on compatibility, transport, and runtime injection.

## Execution Modes

### Semantic Graph QA

```text
user -> semantic layer -> route -> LPG/RDF specialist -> answer generator
```

Use this as the default path for graph-grounded questions. It is the clearest
path for ontology-aware retrieval, candidate review, and evidence tracking.

### Parallel Debate

```text
user -> debate orchestrator -> graph-specific agents -> shared memory -> synthesis
```

Use this for explicit multi-graph or multi-database comparison. It is not the
first-run default. Runtime payloads should report readiness and degraded state
when some graph agents are skipped.

### Legacy Router

```text
user -> router -> specialist -> supervisor -> answer
```

This path exists for compatibility. New graph QA behavior should move through
the semantic query path unless a compatibility test requires otherwise.

## Graph CoT Lane

`query_mode="graph_cot"` keeps the public semantic API surface, but its internal
lane is more explicit:

```text
user question
  -> semantic layer
  -> query supervisor
  -> text-to-cypher agent
  -> answer generation agent
  -> answer guardrail agent
  -> final response
```

Typed artifacts live in `src/seocho/query/graph_cot_contracts.py`. Per-agent
reasoning and tool specs live in `src/seocho/query/graph_cot_design.py`.

## Intent-First Graph-RAG Contract

Semantic QA should move toward an explicit intent-to-evidence contract:

| Field | Purpose |
|---|---|
| `intent_id` | stable name for the answer task |
| `required_relations` | graph relations needed to answer |
| `required_entity_types` | entity types that must be resolved |
| `focus_slots` | values the answer must fill |
| `selected_triples` | evidence selected from graph context |
| `slot_fills` | values found in evidence |
| `missing_slots` | answer gaps that remain |
| provenance and confidence | why the answer can be trusted |

This keeps retrieval accountable to answerability instead of vague local
similarity.

See `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md` for the full handoff contract.

## Cypher Safety

Cypher generation and templates must stay validation-first:

- validate identifiers before execution
- prefer `elementId()` over deprecated internal ID assumptions
- distinguish empty result from connector failure
- return insufficient evidence when required slots are missing
- preserve graph/database/workspace scope in trace metadata

Do not treat a syntactically valid query as proof that the answer is supported.

## Entity And Vocabulary Layer

The vocabulary layer reduces brittle keyword matching. It turns labels, aliases,
and rule-derived terms into a governed lookup surface.

| Plane | Responsibility |
|---|---|
| control plane | lifecycle, promotion gates, workspace overrides |
| data plane | term generation, enrichment, provenance |
| runtime | lightweight query-time term expansion |
| offline governance | heavier ontology reasoning and OWL inspection |

Offline ontology governance should stay out of request-time code.

## Evidence Standard

A supported answer should make these reviewable:

| Evidence | Why it matters |
|---|---|
| ontology context hash | proves the query used the same contract as indexed data |
| selected graph entities | shows what the query grounded on |
| selected relationships or triples | shows answer support |
| missing slots | prevents false completeness |
| trace metadata | explains routing and tool calls |
| run metadata | makes later debugging possible |

Performance or production claims require live runs against named services and
recorded environment details. Mocks are useful for contract tests, not for
scalability claims.

