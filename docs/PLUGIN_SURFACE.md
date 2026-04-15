# Plugin Surface

SEOCHO exposes **exactly four** extension points. This is the complete
list of places where third-party backends or providers can plug in.

Everything else is an internal implementation detail and is not a
supported extension surface.

## Why only four

SEOCHO is ontology-aligned middleware. The alignment contract between
agent behavior, graph-database state, and the ontology schema can only
stay stable if extension points are few and well-defined. Adding a
fifth plugin surface requires an ADR and a version bump — not an ad-hoc
subclass.

## The Four Plugin Surfaces

### 1. `GraphStore` — graph database backend

**Location:** `seocho/store/graph.py`

**Contract:** write nodes and relationships, run Cypher queries, apply
ontology-derived constraints, introspect schema, delete by source,
count by source, close resources.

**Ships with:**

- `Neo4jGraphStore` — Neo4j / DozerDB over Bolt, production use
- `LadybugGraphStore` — embedded LadybugDB, zero-config local use

**Adding a backend:** subclass `GraphStore`, implement every abstract
method, verify against `seocho/tests/test_ladybug_store.py`-style
integration tests. The `GraphStore.ensure_constraints(ontology)` method
is where your backend consumes the ontology; everything else is
Cypher-shaped.

### 2. `VectorStore` — vector similarity backend

**Location:** `seocho/store/vector.py`

**Contract:** add documents with text + metadata, search by query text
or embedding, delete, count, persist, load.

**Ships with:**

- `FAISSVectorStore` — in-memory FAISS
- `LanceDBVectorStore` — persistent LanceDB

**Adding a backend:** subclass `VectorStore`, implement every abstract
method. Vector search is the optional hybrid retrieval layer alongside
graph traversal.

### 3. `LLMBackend` — chat-completion provider

**Location:** `seocho/store/llm.py`

**Contract:** synchronous `complete(system, user, ...)` and async
`acomplete(...)` returning a structured `LLMResponse`. Must surface
usage metadata and a stable `.text` field.

**Ships with:**

- `OpenAIBackend`, `DeepSeekBackend`, `KimiBackend`, `GrokBackend`
- `OpenAICompatibleBackend` base for other OpenAI-schema providers

**Adding a backend:** prefer subclassing `OpenAICompatibleBackend` if
your provider exposes an OpenAI-compatible API. Otherwise subclass
`LLMBackend` directly. Extraction, linking, and answer synthesis all
call through this interface.

### 4. `EmbeddingBackend` — embedding provider

**Location:** `seocho/store/llm.py`

**Contract:** `embed(texts, *, model=None) -> List[List[float]]`. Must
be deterministic for a given (model, input) pair.

**Ships with:**

- `OpenAICompatibleEmbeddingBackend`

**Adding a backend:** subclass `EmbeddingBackend`. Used by
`EmbeddingLinker` (`seocho/index/linker.py`) for cross-record
relatedness and by `VectorStore` implementations for indexing.

## What is *not* a plugin surface

These are **internal contracts** that we reserve the right to rewrite
between minor versions. Do not subclass or monkey-patch them for
extensibility:

- Indexing pipeline (`seocho/index/pipeline.py`) — use callbacks instead
- Query flow (`seocho/query/*`) — use the agent factory hooks instead
- Agent factories (`seocho/agent/factory.py`) — use `Seocho.agent(kind)`
- Ontology internals (`ontology_serialization`, `ontology_artifacts`,
  `ontology_governance`) — public API is `Ontology` only
- Runtime bundle format (`seocho/runtime_bundle.py`) — versioned schema,
  not a subclass point
- Tracing (`seocho/tracing.py`) — configure via env vars, not subclasses

## Adding a new backend — checklist

1. Subclass the relevant ABC in `seocho/store/`.
2. Implement every abstract method. Raise `NotImplementedError` only
   where the abstract method explicitly permits it; otherwise provide
   a real implementation.
3. Add integration tests under `seocho/tests/` that match the pattern
   of the existing backend tests.
4. Update `seocho/__init__.py` module exports if you want the class
   importable from the top level.
5. Update this document (`docs/PLUGIN_SURFACE.md`) with the new
   backend under the relevant section.
6. Open a PR. No ADR needed for new *instances* of an existing plugin
   surface. New *plugin surfaces* require an ADR.

## Stability guarantee

- The four ABCs listed above follow semantic versioning at the package
  level. Breaking changes require a major version bump.
- New optional methods on an ABC ship with a default implementation so
  existing subclasses continue to work.
- Removing a plugin surface requires an ADR and a deprecation cycle of
  at least one minor version.
