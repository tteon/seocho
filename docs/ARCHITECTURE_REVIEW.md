# SEOCHO Architecture Review

**Date**: 2026-02-08
**Overall Grade**: B- (before improvements) → B+ (after Phase 1-4)

---

## Scoreboard

| Dimension | Grade | Notes |
|-----------|-------|-------|
| **Module Cohesion** | A- | Clean module boundaries, single-responsibility well observed |
| **Design Patterns** | A | Ontology-driven extraction, closure-bound agents, Society-of-Mind debate |
| **Configuration** | B+ | Centralized in `config.py`, but no startup validation pre-fix |
| **API Design** | B | Clean REST endpoints, missing request correlation and structured errors |
| **Error Handling** | D+ → B+ | Was: bare `except Exception` everywhere. Now: typed hierarchy + retry |
| **Testing** | F → C+ | Was: no meaningful unit tests. Now: 10 test files covering core modules |
| **Pipeline Resilience** | D → B | Was: silent failure, no aggregation. Now: `PipelineResult`, continue-on-error |
| **Observability** | B+ | Opik tracing is well-integrated and opt-in |
| **Security** | B | Label validation prevents injection, CORS configured, query length limited |
| **Scalability** | C+ → B- | Bounded caches prevent memory leaks, but pipeline is still synchronous |

---

## Strengths to Preserve

### 1. Ontology-Driven Extraction (A)
The `OntologyPromptBridge` pattern is excellent. Ontology YAML drives extraction prompts via Jinja2, making the system schema-aware without hardcoding entity types.

```
Ontology YAML → OntologyPromptBridge.render_extraction_context() → Jinja2 template → LLM
```

### 2. Closure-Bound Agent Tools (A)
`AgentFactory.create_db_agent()` uses closures to bind `db_name` at creation time. Each agent's `query_db` tool only queries its bound database — elegant and secure.

```python
# agent_factory.py: closure captures _db
_db = db_name
@function_tool
def query_db(context, query):
    return connector.run_cypher(query, database=_db)
```

### 3. Society-of-Mind Debate Pattern (A-)
`DebateOrchestrator` implements parallel fan-out via `asyncio.gather`, collects results in `SharedMemory`, then synthesizes via Supervisor. Clean separation of concerns.

### 4. Opik Tracing is Properly Opt-In (A-)
All tracing gated behind `OPIK_ENABLED` with no-op wrappers. The `tracing.py` module provides a clean abstraction layer.

### 5. Centralized Config (B+)
`config.py` is the single source of truth for Neo4j credentials, Opik settings, and the `DatabaseRegistry` singleton.

---

## Anti-Patterns Found (Pre-Fix)

### 1. Silent Error Swallowing (D+)
**Files**: `extractor.py:60`, `linker.py:51`, `vector_store.py:35`, `pipeline.py:151`

Every module had bare `except Exception` that logged and silently returned empty/default values. Errors were invisible to callers.

```python
# BEFORE (extractor.py)
except Exception as e:
    logger.error("Error during extraction: %s", e)
    return {"nodes": [], "relationships": []}  # Silent failure!
```

**Fix**: Typed exceptions (`OpenAIAPIError`, `ExtractionError`) that propagate to callers. Retry decorators handle transient failures.

### 2. Undefined Variable Bug (F)
**File**: `agent_server.py:125`

```python
@function_tool
def search_vector_tool(query: str) -> str:
    return faiss_manager.search(query)  # NameError at runtime!
```

`faiss_manager` was never defined. `VectorAgent` was broken in production.

**Fix**: Instantiated `faiss_manager = VectorStore(api_key=...)` as a module singleton.

### 3. No Config Validation (D)
**File**: `agent_server.py:34`

Server started without checking if `OPENAI_API_KEY` existed. First API call would fail with an opaque OpenAI SDK error.

**Fix**: `validate_config()` called during `_startup()`, raises `MissingAPIKeyError` with clear message.

### 4. Unbounded Caches (D)
**Files**: `shared_memory.py:32`, `deduplicator.py:31`

`_query_cache` and `_canonical_embeddings` were plain `dict`s that grew without limit. A long-running server or large dataset could exhaust memory.

**Fix**: `OrderedDict` with LRU eviction at `MAX_QUERY_CACHE_SIZE=100` and `MAX_CANONICAL_EMBEDDINGS=10000`.

### 5. No Request Correlation (C-)
**File**: `agent_server.py`

API errors returned generic 500s with no request ID. Debugging in production required correlating logs by timestamp.

**Fix**: `RequestIDMiddleware` reads/generates `X-Request-ID`, stores in `ContextVar`, includes in structured error responses.

### 6. Pipeline Stops on First Error (D)
**File**: `pipeline.py:151`

Single-item failure in `process_item()` was caught but not aggregated. `run()` returned `None`, so callers had no visibility into partial failures.

**Fix**: `PipelineResult` dataclass with `items_processed`, `items_failed`, `errors` list. Pipeline continues on failure and reports aggregate results.

### 7. Label Validation Was Permissive (C+)
**File**: `graph_loader.py:18`

Invalid labels fell back to `"Entity"` silently. This masked data quality issues and made debugging harder.

**Fix**: `_validate_label()` now raises `InvalidLabelError` — fails fast rather than silently corrupting data.

---

## Improvement Roadmap (Implemented)

### Phase 1: Foundations — Exceptions + Retry + Bug Fix
- [x] `extraction/exceptions.py` — Custom exception hierarchy (10 typed exceptions)
- [x] `extraction/retry_utils.py` — `@openai_retry` and `@neo4j_retry` decorators
- [x] Updated 6 modules with typed exceptions and retry
- [x] Fixed `faiss_manager` undefined variable bug
- [x] Added `validate_config()` to startup

### Phase 2: DI + Testing Infrastructure
- [x] `extraction/dependencies.py` — FastAPI DI providers
- [x] `extraction/tests/conftest.py` — Shared fixtures
- [x] 10 test files covering exceptions, retry, config, graph_loader, shared_memory, pipeline, dedup, API, middleware, error responses

### Phase 3: Pipeline Resilience
- [x] `PipelineResult` dataclass with error aggregation
- [x] Pipeline `run()` continues on item failure, returns aggregate result
- [x] Bounded LRU caches in `SharedMemory` and `EntityDeduplicator`

### Phase 4: API Hardening
- [x] `extraction/middleware.py` — `RequestIDMiddleware` with `ContextVar`
- [x] Structured `ErrorResponse` model with `error_code`, `message`, `request_id`
- [x] `@app.exception_handler(SeochoError)` with HTTP status mapping

---

## Before/After Code Patterns

### Error Handling

```python
# BEFORE
try:
    response = self.client.chat.completions.create(...)
    return json.loads(content)
except json.JSONDecodeError as e:
    logger.error("Failed to parse: %s", e)
    return {"nodes": [], "relationships": []}
except Exception as e:
    logger.error("Error: %s", e)
    return {"nodes": [], "relationships": []}

# AFTER
@openai_retry
def extract_entities(self, text, category, extra_context):
    try:
        response = self.client.chat.completions.create(...)
    except Exception as e:
        raise OpenAIAPIError(f"OpenAI call failed: {e}") from e
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"JSON parse failed: {e}") from e
```

### Pipeline Resilience

```python
# BEFORE
def run(self):
    for item in raw_data:
        self.process_item(item)  # stops on first error

# AFTER
def run(self) -> PipelineResult:
    result = PipelineResult()
    for idx, item in enumerate(raw_data):
        try:
            self.process_item(item)
            result.items_processed += 1
        except Exception as e:
            result.items_failed += 1
            result.errors.append({"item_id": item_id, ...})
    return result
```

### API Error Responses

```python
# BEFORE
raise HTTPException(status_code=500, detail="Check server logs")

# AFTER — structured response with request ID
{
    "error": {
        "error_code": "OpenAIAPIError",
        "message": "OpenAI rate limited",
        "request_id": "abc-123-def"
    }
}
```

---

## Remaining Opportunities (Future Work)

| Priority | Item | Impact |
|----------|------|--------|
| High | Async pipeline (`asyncio.gather` for items) | 3-5x throughput |
| High | Integration tests with Docker Neo4j | Catch schema/query regressions |
| Medium | Rate-limit aware batching for OpenAI calls | Cost reduction |
| Medium | Circuit breaker for Neo4j (stop retrying when DB is down) | Faster failure detection |
| Medium | Health check endpoint (`GET /health`) | Load balancer integration |
| Low | Prometheus metrics export | Production monitoring |
| Low | Schema versioning with migrations | Prevent schema drift |
| Low | Webhook/event notifications on pipeline completion | Automation |

---

## File Inventory (Changes Made)

### New Files (11)
| File | Purpose |
|------|---------|
| `extraction/exceptions.py` | Custom exception hierarchy |
| `extraction/retry_utils.py` | Tenacity retry decorators |
| `extraction/dependencies.py` | FastAPI DI providers |
| `extraction/middleware.py` | Request ID middleware |
| `extraction/tests/conftest.py` | Shared test fixtures |
| `extraction/tests/test_exceptions.py` | Exception hierarchy tests |
| `extraction/tests/test_retry.py` | Retry decorator tests |
| `extraction/tests/test_config_validation.py` | Config validation tests |
| `extraction/tests/test_graph_loader.py` | Label validation + loading tests |
| `extraction/tests/test_shared_memory.py` | Cache behavior + LRU eviction tests |
| `extraction/tests/test_pipeline_resilience.py` | PipelineResult tests |
| `extraction/tests/test_deduplicator.py` | Bounded cache eviction tests |
| `extraction/tests/test_api_endpoints.py` | API endpoint tests |
| `extraction/tests/test_middleware.py` | Request ID middleware tests |
| `extraction/tests/test_error_responses.py` | Structured error response tests |
| `docs/ARCHITECTURE_REVIEW.md` | This document |

### Modified Files (12)
| File | Changes |
|------|---------|
| `extraction/extractor.py` | `@openai_retry`, `OpenAIAPIError`, `ExtractionError` |
| `extraction/linker.py` | `@openai_retry`, `OpenAIAPIError`, `LinkingError` |
| `extraction/vector_store.py` | `@openai_retry`, `OpenAIAPIError` on `embed_text()` |
| `extraction/graph_loader.py` | `@neo4j_retry`, `Neo4jConnectionError`, `InvalidLabelError` (raises instead of fallback) |
| `extraction/database_manager.py` | `@neo4j_retry`, `InvalidDatabaseNameError`, `Neo4jConnectionError` |
| `extraction/schema_manager.py` | `@neo4j_retry`, `Neo4jConnectionError` |
| `extraction/agent_server.py` | Fixed `faiss_manager` bug, added exception handler, `validate_config()`, `RequestIDMiddleware` |
| `extraction/config.py` | Added `validate_config()` |
| `extraction/pipeline.py` | `PipelineResult`, error aggregation in `run()`, exceptions propagate from `process_item()` |
| `extraction/shared_memory.py` | `OrderedDict` with LRU eviction (MAX_QUERY_CACHE_SIZE=100) |
| `extraction/deduplicator.py` | `OrderedDict` with eviction (MAX_CANONICAL_EMBEDDINGS=10000) |
| `extraction/requirements.txt` | Added `tenacity`, `pytest-asyncio`, `pytest-cov`, `httpx` |
