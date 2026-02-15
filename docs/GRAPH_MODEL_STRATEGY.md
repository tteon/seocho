# Graph Model Strategy (Document -> Ontology -> Graph)

## 1. Goal

When a user uploads documents in the frontend, SEOCHO should:

1. extract ontology candidates with minimal manual domain assumptions,
2. represent knowledge as graph structures optimized for retrieval,
3. store/query in DozerDB,
4. keep ontology governance in Owlready2 (offline-focused).

## 2. Fixed Constraints (Consensus)

- Runtime agent stack: OpenAI Agents SDK
- Trace/eval: Opik
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

- nodes: `Document`, `Section`, `Chunk`
- edges: `HAS_SECTION`, `HAS_CHUNK`, `PART_OF`
- purpose: preserve source hierarchy and provenance

Layer B — Entity Interaction Graph:

- nodes: `Entity` (+ optional typed labels)
- edges: `HAS_ENTITY`, `RELATES_TO`
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
3. Generate chunk embeddings
4. Extract entities + relations
5. Build layered graph (A/B; optionally C)
6. Infer/validate rule profile (`/rules/infer`, `/rules/validate`)
7. Save rule profile (`/rules/profiles`)
8. Export constraint plan (`/rules/export/cypher`)
9. Run offline ontology consolidation with Owlready2
10. Publish query-ready graph in DozerDB

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
