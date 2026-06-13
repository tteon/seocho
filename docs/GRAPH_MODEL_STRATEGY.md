# Graph Model Strategy (Document -> Ontology -> Graph)

## 1. Goal

When a user uploads documents in the frontend, SEOCHO should:

1. extract ontology candidates with minimal manual domain assumptions,
2. represent knowledge as graph structures optimized for retrieval,
3. store/query in DozerDB,
4. keep ontology governance in Owlready2 (offline-focused).

## 2. Fixed Constraints (Consensus)

- Runtime agent stack: OpenAI Agents SDK
- Trace/eval contract: vendor-neutral (`none|console|jsonl|opik`)
- Preferred team backend: Opik
- Canonical neutral artifact: JSONL
- Graph backend: DozerDB
- MVP tenancy: single-tenant, `workspace_id` propagated
- Ontology reasoning:
  - runtime: app-level policy + lightweight checks
  - offline: Owlready2 reasoning/validation pipeline

## 3. GraphRAG Pattern Families (What to Borrow)

Based on graphrag.com pattern catalog:

- `Domain Graph`: structured business entities/relations
- `Lexical Graph`: document/chunk graph
- `Parent-Child Lexical Graph`: child chunk embedding + parent context retrieval
- `Lexical Graph with Extracted Entities`: chunk + entity + relation triples
- `Lexical Graph with Extracted Entities and Community Summaries`: adds community nodes for global questions
- `Lexical Graph with Hierarchical Structure`: chapter/section/subsection from original docs

Retriever alignment:

- local/entity exploration: Local Retriever
- richer context around chunks/entities: Graph-Enhanced Vector Search
- global summarization across corpus: Global Community Summary Retriever
- deterministic structured querying: Text2Cypher (with guardrails)

## 4. Recommended Representation Stack for SEOCHO

## 4.1 Layered Graph (Single Physical Store in DozerDB)

Layer A — Document Structure Graph:

- nodes: `Document`, `DocumentVersion`, `Section`, `Chunk`
- edges: `HAS_VERSION`, `CURRENT_VERSION`, `HAS_SECTION`, `HAS_CHUNK`, `PART_OF`, `NEXT`
- purpose: preserve source hierarchy, immutable ingest snapshots, and provenance

Layer B — Entity Interaction Graph:

- nodes: `Entity` (+ optional typed labels)
- edges: `MENTIONS`, `HAS_ENTITY`, `RELATES_TO`
- purpose: semantic connectivity beyond pure vector proximity

Layer C — Community / Topic Graph:

- nodes: `Community` (level, summary, full_content, weight)
- edges: `IN_COMMUNITY`, `PARENT_COMMUNITY`
- purpose: answer global or corpus-level questions

Inference: Start with A + B first. Add C once corpus size and global-query demand justify preprocessing cost.

## 4.2 Why This Fits “Minimize Domain Knowledge”

- Begin with lexical/document-first structures (low upfront schema assumptions).
- Extract entities/relations with prompt-guided but not hard-coded ontology.
- Stabilize ontology iteratively from observed graph + rule violations.
- Only promote stable concepts into curated ontology classes/properties.

## 4.3 Local SDK Ingest Contract

For the current local SDK path, treat the layers this way:

- `Document`: logical source-of-truth anchor for one memory/document id
- `DocumentVersion`: immutable ingest snapshot keyed by content/version metadata
- `Section`: document hierarchy anchor inferred from source headings or supplied
  explicitly by the caller
- `Chunk`: vector retrieval unit keyed by `chunk_id`
- `Entity`: graph reasoning and cross-document join unit

Operational rule:

- chunk embeddings live in the vector backend, not as the primary graph answer
  substrate
- the vector row must carry `workspace_id`, `memory_id`, `document_id`,
  `version_id`, `chunk_id`, and `section_path`
- graph-grounded answers should retrieve chunks first, then expand through the
  graph using the preserved join keys and provenance edges
- local `Seocho.add_graph(...)` should reuse the same ontology validation,
  provenance shaping, and chunk/vector join contract for caller-supplied graph
  payloads instead of bypassing the layered memory graph

## 4.4 Qualification / Curation Plane

Do not treat observed ingest and canonical serving as the same persistence
problem.

- `add(...)` / `add_graph(...)` produce the observed graph
- a tabular qualification store records observed entities, observed relations,
  chunks, cases, and curation decisions
- canonical serving projection is built later from those decisions and written
  back to the graph store with distinct canonical IDs

Recommended split:

- semantic/control plane: ontology + indexing design + curation design
- qualification plane: SQLite-default tabular store for cases and decisions
- serving plane: DozerDB graph projection for canonical entities and relations

DuckDB remains useful as an optional analytics backend, but not as the default
mutable curation store.

This keeps ontology-sensitive merge rules explicit and reviewable while letting
Graph-RAG answer from the projected canonical graph instead of destructively
rewriting raw observed ingest.

## 5. Owlready2 Role (Important Boundary)

Use Owlready2 as **ontology control plane**, not as hot-path retriever:

- ingest candidate ontology from extraction outputs
- run consistency/classification/rule checks offline
- emit governance artifacts:
  - OWL/Turtle snapshots
  - SHACL-like rules
  - compiled DB constraint plans

Runtime request path should not depend on synchronous heavyweight reasoning.

## 6. End-to-End Pipeline (Frontend Upload Use Case)

1. Upload document(s) in frontend
2. Parse structure (document -> sections -> chunks)
3. Create immutable `DocumentVersion` + `Chunk` records for this ingest pass
4. Generate chunk embeddings keyed by `chunk_id`
5. Extract entities + relations
6. Build layered graph (A/B; optionally C)
7. Infer/validate rule profile (`/rules/infer`, `/rules/validate`)
8. Save rule profile (`/rules/profiles`)
9. Export constraint plan (`/rules/export/cypher`)
10. Run offline ontology consolidation with Owlready2
11. Publish query-ready graph in DozerDB

## 7. Retrieval Routing Policy

- Question asks “overall trends/themes/synthesis”:
  - prefer Global Community Summary Retriever (Layer C)
- Question asks about specific entities/events/links:
  - prefer Local Retriever / Graph-Enhanced Vector Search (Layer B + chunk context)
- Question asks explicit structured facts:
  - prefer Text2Cypher with strict guardrails (schema injection, read-only, retry-on-error)

## 8. Guardrails

- provenance mandatory on extracted triples (`source_doc`, `chunk_id`, confidence)
- ontology change approval path (proposed -> validated -> accepted)
- rule profile versioning and rollback
- Text2Cypher safety:
  - read-only mode by default
  - syntax/error retry loop
  - bounded result size

## 8b. Entity Identity Contract (seocho-uxs)

Cross-document node merging needs a *distinguishing point*: when are two
mentions the same real-world entity?

- **Default — single key.** A node type's identity is its first
  `unique=True` property (else its `id`). Two mentions merge when that one
  value matches. This is wrong for **dimension-bearing entities**: two
  documents each naming a `FinancialMetric` "Total revenue" (one PTC's, one
  Tesla's) collapse onto `name` — and the second write overwrites the first
  value (silent last-writer-wins on the embedded store).

- **Composite identity — `NodeDef.identity_keys`.** Declare the ordered
  tuple that identifies the entity, e.g.

  ```python
  "FinancialMetric": NodeDef(
      properties={"name": P(str, unique=True), "company": P(str),
                  "year": P(str), "value": P(str)},
      identity_keys=["name", "company", "year"],
  )
  ```

  The indexing pipeline (`seocho.index.identity.apply_identity_keys`) rewrites
  each such node's `id` to a deterministic composite
  (`financialmetric|total revenue|ptc|2023`) before the write and remaps
  relationship endpoints. PTC's and Tesla's revenue then key on distinct ids
  and stay separate. `to_cypher_constraints` emits one composite
  `REQUIRE (n.name, n.company, n.year) IS UNIQUE` instead of a per-member
  `UNIQUE` (so distinct entities sharing `name` are not rejected), and the
  Ladybug store keys its MERGE on the composite `id` rather than the bare
  name PK.

- **To distinguish, declare what distinguishes.** The discriminating
  dimensions must be node *properties* listed in `identity_keys` (add
  `company`/`issuer`/`year` to the node, not just to a linked edge).

The structural end-state for metrics is the reified Observation model with
explicit dimensions (ADR-0103 H4); `identity_keys` is the general-purpose
contract that works for any entity type today.

**Safety net (seocho-uxs.1).** For node types *without* declared
`identity_keys`, a single-key MERGE still silently last-writer-wins on a value
clash. Both stores now surface this: when a MERGE lands on an existing node
whose user-facing property already holds a different non-empty value, the
write summary carries a non-fatal `merge_conflicts` entry
(`{label, key, property, existing, incoming, source_id}`), propagated to
`IndexingResult.merge_conflicts`. Ingestion still succeeds — the signal is
advisory, so silent divergence becomes auditable instead of invisible.

## 9. Immediate Build Order

1. Keep current rules APIs as baseline contract
2. Add frontend flow: upload -> infer rules -> validate -> save profile
3. Add graph viewer over Document/Chunk/Entity layers
4. Add offline Owlready2 worker job for ontology consolidation
5. Add query router using question type to pick retriever strategy

## References

- GraphRAG home: https://graphrag.com/
- GraphRAG pattern catalog: https://graphrag.com/reference/
- Intro to GraphRAG: https://graphrag.com/concepts/intro-to-graphrag/
- Intro to KG: https://graphrag.com/concepts/intro-to-knowledge-graphs/
- Local Retriever: https://graphrag.com/reference/graphrag/local-retriever/
- Global Community Summary Retriever: https://graphrag.com/reference/graphrag/global-community-summary-retriever/
- Graph-Enhanced Vector Search: https://graphrag.com/reference/graphrag/graph-enhanced-vector-search/
- Text2Cypher: https://graphrag.com/reference/graphrag/text2cypher/
- Owlready2 docs: https://owlready2.readthedocs.io/
- Owlready2 reasoning: https://owlready2.readthedocs.io/en/v0.49/reasoning.html
