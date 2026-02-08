# LanceDB Migration Plan

> Migrate from FAISS (`faiss-cpu`) to LanceDB as the vector storage backend.

## Motivation

| Concern | FAISS (current) | LanceDB (target) |
|---------|-----------------|-------------------|
| **Persistence** | Manual: `faiss.write_index` + pickle metadata | Built-in: Arrow-native tables on disk |
| **Metadata** | Separate `doc_map` dict + pickle file | Co-located with vectors in same table |
| **Hybrid search** | Vector-only (L2) | Vector + full-text keyword search |
| **Storage format** | Flat binary + pickle | Apache Arrow / Lance columnar |
| **Neo4j Arrow integration** | N/A — requires serialization | Zero-copy potential via Arrow IPC |
| **Dependency weight** | `faiss-cpu` (C++ binary, ~50 MB) | `lancedb` (pure Python + Rust, lighter) |
| **Filtering** | Post-hoc Python filtering | SQL-like `WHERE` clauses during search |
| **Scalability** | In-memory only | Disk-backed, memory-mapped |

### Key user rationale
- Hybrid search (vector + keyword) in a single query
- Arrow-native storage enables future zero-copy data transfer to Neo4j via Arrow Flight
- Embedded DB — no external service to manage

---

## Current FAISS Surface Area

### Files that import or use FAISS

| File | Usage | Migration impact |
|------|-------|------------------|
| `extraction/vector_store.py` | Core `VectorStore` class: IndexFlatL2, add, search, save/load | **Full rewrite** |
| `extraction/agent_server.py` | `faiss_manager = VectorStore(...)` + `search_vector_tool` | Import-only (uses `VectorStore` API) |
| `extraction/deduplicator.py` | `vector_store.embed_text()` only | **No change** (doesn't touch FAISS) |
| `extraction/pipeline.py` | Creates `VectorStore(api_key=...)` | Import-only |
| `extraction/ingest_finder.py` | Creates `VectorStore(api_key=...)` | Import-only |
| `extraction/dependencies.py` | `get_vector_store()` DI provider | Import-only |
| `extraction/tests/test_deduplicator.py` | Mocks `faiss` module | Update mock list |
| `extraction/tests/test_api_endpoints.py` | Patches `vector_store.faiss` | Update patches |

### VectorStore public API (must be preserved)

```python
class VectorStore:
    def __init__(self, api_key: str, dimension: int = 1536): ...
    def embed_text(self, text: str) -> List[float]: ...       # OpenAI — unchanged
    def add_document(self, doc_id: str, text: str): ...       # Embed + store
    def save_index(self, output_dir: str): ...                # Persist to disk
    def load_index(self, input_dir: str): ...                 # Load from disk
    def search(self, query: str, k: int = 3) -> List[dict]:  # Vector search
```

---

## Target Architecture

### New VectorStore implementation

```python
import lancedb
import pyarrow as pa

class VectorStore:
    """LanceDB-backed vector store with hybrid search."""

    TABLE_NAME = "documents"

    def __init__(self, api_key: str, dimension: int = 1536, db_path: str = "./vectors_db"):
        self.client = wrap_openai_client(OpenAI(api_key=api_key))
        self.dimension = dimension
        self.db = lancedb.connect(db_path)
        self._table = None  # lazy init

    def _get_or_create_table(self) -> lancedb.table.Table:
        if self._table is not None:
            return self._table
        if self.TABLE_NAME in self.db.table_names():
            self._table = self.db.open_table(self.TABLE_NAME)
        return self._table

    def embed_text(self, text: str) -> List[float]:
        # Unchanged — still uses OpenAI API
        ...

    def add_document(self, doc_id: str, text: str):
        embedding = self.embed_text(text)
        data = [{"id": doc_id, "text": text, "vector": embedding}]
        table = self._get_or_create_table()
        if table is None:
            # Create table on first insert
            self._table = self.db.create_table(self.TABLE_NAME, data=data)
        else:
            table.add(data)

    def search(self, query: str, k: int = 3) -> List[dict]:
        table = self._get_or_create_table()
        if table is None:
            return []
        embedding = self.embed_text(query)
        results = table.search(embedding).limit(k).to_pandas()
        return [{"id": row["id"], "text": row["text"]} for _, row in results.iterrows()]

    def hybrid_search(self, query: str, k: int = 3) -> List[dict]:
        """Vector + full-text hybrid search (new capability)."""
        table = self._get_or_create_table()
        if table is None:
            return []
        embedding = self.embed_text(query)
        results = (
            table.search(embedding, query_type="hybrid")
            .text(query)
            .limit(k)
            .to_pandas()
        )
        return [{"id": row["id"], "text": row["text"]} for _, row in results.iterrows()]

    def save_index(self, output_dir: str):
        # No-op: LanceDB persists automatically to db_path
        logger.info("LanceDB auto-persists to %s", self.db.uri)

    def load_index(self, input_dir: str):
        # No-op: LanceDB loads automatically from db_path
        logger.info("LanceDB auto-loads from %s", self.db.uri)
```

### Key design decisions

1. **`db_path` parameter replaces `output_dir`/`input_dir`** — LanceDB manages its own persistence directory. `save_index`/`load_index` become no-ops for backward compat.

2. **Full text stored in table** — Currently only `text_preview` (first 50 chars) is stored. With LanceDB's columnar storage, store the full text to enable keyword search.

3. **`hybrid_search()` as new method** — Backward compatible: existing `search()` stays vector-only. New `hybrid_search()` adds keyword+vector fusion.

4. **Lazy table creation** — Table created on first `add_document()`, not in `__init__`. Allows connecting to existing DBs without errors.

---

## Migration Steps

### Step 1: Update dependencies

```diff
# pyproject.toml
dependencies = [
-   "faiss-cpu",
+   "lancedb>=0.4",
    "pyarrow",   # already present
    ...
]
```

```diff
# extraction/requirements.txt
-faiss-cpu
+lancedb>=0.4
```

### Step 2: Rewrite `extraction/vector_store.py`

- Replace `import faiss` with `import lancedb`
- Remove `numpy` usage for vector manipulation (LanceDB handles natively)
- Remove pickle-based metadata (`doc_map`, `documents` dicts)
- Replace `IndexFlatL2` with LanceDB table
- Add `hybrid_search()` method
- Keep `embed_text()` unchanged (OpenAI dependency)
- Keep `@openai_retry` decorator on `embed_text()`

### Step 3: Update `extraction/agent_server.py`

- Update `search_vector_tool` to optionally use `hybrid_search()`:
  ```python
  # Could expose hybrid search as a separate tool or parameter
  results = faiss_manager.search(query)  # still works as-is
  ```
- Consider adding `db_path` to config.py for centralized path management

### Step 4: Update `extraction/config.py`

```python
# Add vector store path config
VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", "./vectors_db")
```

### Step 5: Update `extraction/pipeline.py`

- Pass `db_path` to `VectorStore` constructor:
  ```python
  self.vector_store = VectorStore(api_key=cfg.openai_api_key, db_path=VECTOR_DB_PATH)
  ```

### Step 6: Update `extraction/ingest_finder.py`

- Same as pipeline: pass `db_path`

### Step 7: Update tests

| Test file | Change |
|-----------|--------|
| `tests/test_deduplicator.py` | Remove `"faiss"` from mock list (deduplicator doesn't use it directly) |
| `tests/test_api_endpoints.py` | Replace `patch("vector_store.faiss")` with `patch("vector_store.lancedb")` |
| NEW: `tests/test_vector_store.py` | Test `add_document`, `search`, `hybrid_search`, persistence |

### Step 8: Update Dockerfile

No change needed — `lancedb` installs via pip/uv like any Python package (no C++ build dependency unlike `faiss-cpu`).

### Step 9: Data migration (if existing FAISS indices exist)

```python
# One-time migration script: scripts/migrate_faiss_to_lance.py
import faiss
import pickle
import lancedb

# Load old FAISS data
index = faiss.read_index("vectors.index")
with open("vectors_meta.pkl", "rb") as f:
    meta = pickle.load(f)

# Reconstruct vectors from FAISS index
vectors = faiss.rev_swig_ptr(index.get_xb(), index.ntotal * index.d)
vectors = vectors.reshape(index.ntotal, index.d)

# Write to LanceDB
db = lancedb.connect("./vectors_db")
data = [
    {"id": meta["doc_map"][i], "text": doc.get("text_preview", ""), "vector": vectors[i].tolist()}
    for i, doc in enumerate(meta["documents"])
]
db.create_table("documents", data=data)
```

---

## Arrow-Native Benefits (Future)

### Neo4j Arrow Flight integration

Once data is in Arrow format via LanceDB:

```python
# Future: zero-copy export for Neo4j import
table = db.open_table("documents")
arrow_table = table.to_arrow()  # pa.Table — zero-copy

# Neo4j Arrow Flight (when available)
# neo4j_arrow.write(arrow_table, database="kgnormal")
```

### Cross-module Arrow sharing

```
LanceDB table → pa.Table → Neo4j Arrow Flight
                         → pandas (zero-copy via .to_pandas())
                         → Parquet export (zero-copy via pq.write_table())
```

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LanceDB API changes (pre-1.0) | Medium | Medium | Pin version `>=0.4,<1.0` |
| Performance regression on large indices | Low | Medium | Benchmark against FAISS before merge |
| Full-text index build time | Low | Low | FTS index creation is async in LanceDB |
| Existing FAISS indices lost | Medium | Low | Migration script + keep old files |
| `hybrid_search` quality tuning | Medium | Low | Expose reranking weights as config |

## Estimated Scope

- **Files modified**: 6 (vector_store.py, agent_server.py, config.py, pipeline.py, ingest_finder.py, requirements.txt/pyproject.toml)
- **Files created**: 2 (tests/test_vector_store.py, scripts/migrate_faiss_to_lance.py)
- **Tests updated**: 2 (test_deduplicator.py, test_api_endpoints.py)
- **Breaking changes**: None — public API preserved, `save_index`/`load_index` become no-ops
- **New capability**: `hybrid_search()` method for vector + keyword fusion
