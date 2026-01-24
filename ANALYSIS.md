# SEOCHO Repository Analysis & Architecture Review

**Date**: 2025-01-24  
**Primary Focus**: `graphrag-dev` branch  
**Analyst**: Warp Agent

---

## 1. Branch Overview

| Branch | Purpose | Status | Commits Ahead of Main |
|--------|---------|--------|----------------------|
| `main` | Production/stable release | Basic README, initial setup | baseline |
| `graphrag-dev` | **Active development** - Multi-agent orchestration + Data Mesh | 10 commits | +5868/-2893 lines |
| `feature-kgbuild` | Experimental modular architecture with evaluation framework | 4 commits | Divergent (refactored structure) |
| `master` | Legacy initial commit | Deprecated | - |

**Recommendation**: Merge `graphrag-dev` into `main` as it represents significant production-ready features.

---

## 2. Architecture Analysis (`graphrag-dev`)

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           SEOCHO ARCHITECTURE                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐  │
│  │  Streamlit   │───▶│  FastAPI     │───▶│   Multi-Agent System     │  │
│  │  (UI/Trace)  │    │  (8001)      │    │   (OpenAI Agents SDK)    │  │
│  └──────────────┘    └──────────────┘    └──────────────────────────┘  │
│        ▲                    │                        │                   │
│        │                    │              ┌─────────┴─────────┐        │
│        │                    │              ▼                   ▼        │
│        │             ┌──────┴──────┐  ┌─────────┐      ┌────────────┐  │
│   Trace Steps        │ Router Agent│  │ Vector  │      │  Graph     │  │
│                      └──────┬──────┘  │ Agent   │      │  Agent     │  │
│                             │         └────┬────┘      └─────┬──────┘  │
│                    ┌────────┼────────┐     │                 │         │
│                    ▼        ▼        ▼     ▼                 ▼         │
│              ┌─────────┐ ┌──────┐ ┌─────┐ ┌─────┐      ┌──────────┐   │
│              │  Table  │ │ Web  │ │Graph│ │FAISS│      │ GraphDBA │   │
│              │  Agent  │ │Agent │ │Agent│ │Store│      │  Agent   │   │
│              └─────────┘ └──────┘ └─────┘ └─────┘      └────┬─────┘   │
│                                                              │         │
│                                                              ▼         │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │                         DATA LAYER                                 │ │
│  │  ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌──────────┐ │ │
│  │  │  Neo4j     │   │  Neo4j     │   │  Neo4j     │   │ DataHub  │ │ │
│  │  │ (kgnormal) │   │  (kgfibo)  │   │(agent_trace│   │  (GMS)   │ │ │
│  │  │  Baseline  │   │  Financial │   │    logs)   │   │ Metadata │ │ │
│  │  └────────────┘   └────────────┘   └────────────┘   └──────────┘ │ │
│  └───────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Component Breakdown

#### A. **Extraction Pipeline** (`extraction/`)
Core ETL system for knowledge graph construction.

| Component | File | Purpose |
|-----------|------|---------|
| Pipeline Orchestrator | `pipeline.py` | Main execution flow |
| Data Collector | `collector.py` | Mock/HuggingFace data ingestion |
| Entity Extractor | `extractor.py` | LLM-based NER |
| Entity Linker | `linker.py` | Entity resolution & canonicalization |
| Graph Loader | `graph_loader.py` | Neo4j ingestion |
| Vector Store | `vector_store.py` | FAISS embeddings |
| Schema Manager | `schema_manager.py` | Dynamic schema discovery |
| Prompt Manager | `prompt_manager.py` | Jinja2 prompt templates |

#### B. **Agent System** (`extraction/agent_server.py`)
Hierarchical multi-agent architecture using OpenAI Agents SDK.

```
Router Agent
    ├── GraphAgent ──► GraphDBA ──► Neo4j (Text2Cypher)
    ├── VectorAgent ──► FAISS Search
    ├── WebAgent ──► External Search (stub)
    └── TableAgent ──► Structured Data (stub)
            │
            └──► Supervisor (Final Synthesis)
```

#### C. **Evaluation UI** (`evaluation/app.py`)
Streamlit-based agent debugging interface with live flow visualization.

#### D. **Infrastructure** (`docker-compose.yml`)
- **Neo4j (DozerDB)**: Graph storage with APOC + n10s plugins
- **DataHub**: Metadata catalog (GMS + Frontend)
- **Kafka/Zookeeper**: Event streaming for DataHub
- **Elasticsearch**: DataHub search backend
- **MySQL**: DataHub persistence

---

## 3. Strengths

### ✅ Well-Designed Agent Hierarchy
The Router → Specialist → DBA → Supervisor pattern is clean and follows best practices for multi-agent systems.

### ✅ Schema Auto-Discovery
`SchemaManager.update_schema_from_records()` dynamically learns graph schema from extracted data.

### ✅ Multi-Database Support
Clean separation between `kgnormal`, `kgfibo`, and tracing databases.

### ✅ Observability Built-In
- OpenAI trace integration
- Streamlit flow visualization
- Prompt history logging (`prompt_history.json`)

### ✅ Configuration-Driven
Hydra-based config with Jinja2 prompts allows runtime customization without code changes.

---

## 4. Improvement Opportunities

### 🔴 Critical Issues

#### 4.1 Security: Cypher Injection Vulnerability
**File**: `extraction/graph_loader.py:41-46`

```python
query = (
    f"MERGE (n:`{label}` {{id: $id}}) "  # ❌ Dynamic label injection risk
    f"SET n += $props "
    f"RETURN n"
)
```

**Recommendation**: Sanitize labels or use an allowlist.

```python
ALLOWED_LABELS = {"Entity", "Person", "Organization", "Concept", ...}
if label not in ALLOWED_LABELS:
    label = "Entity"  # Fallback
```

#### 4.2 Missing Error Handling in Agent Server
**File**: `extraction/agent_server.py:59`

```python
# ... (FAISSManager, SchemaManager)  # ← These are referenced but never instantiated
```

The `faiss_manager` and `neo4j_conn` are used in tools but initialization code is incomplete:

```python
# Line 109: faiss_manager.search(query)  # ← NameError: faiss_manager not defined
```

**Recommendation**: Add proper initialization:
```python
# Global singletons
faiss_manager = VectorStore(api_key=os.getenv("OPENAI_API_KEY"))
faiss_manager.load_index("output")
neo4j_conn = Neo4jConnector()
```

#### 4.3 Hardcoded Credentials in Docker Compose
**File**: `docker-compose.yml:50-51`

```yaml
MYSQL_ROOT_PASSWORD: datahub  # ❌ Hardcoded
```

**Recommendation**: Use environment variables consistently:
```yaml
MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:-datahub}
```

---

### 🟡 Moderate Issues

#### 4.4 Inefficient Vector Search
**File**: `extraction/vector_store.py:106`

```python
doc_meta = next((d for d in self.documents if d["id"] == doc_id), ...)  # O(N) lookup
```

**Recommendation**: Use dict-based lookup:
```python
self.documents = {}  # Change to dict: {doc_id: metadata}
```

#### 4.5 Test Coverage Gaps
Current tests are mostly smoke tests:

| File | Coverage | Notes |
|------|----------|-------|
| `test_basic.py` | Minimal | Only checks collector returns list |
| `test_api_integration.py` | Incomplete | Mocked endpoint with `pass` |
| `test_tools.py` | Unknown | Not reviewed |

**Recommendation**: Add integration tests for:
- Full pipeline run with mock LLM
- Agent handoff sequences
- Schema manager updates

#### 4.6 Missing Async Optimization
**File**: `extraction/agent_server.py:241`

```python
result = await Runner.run(...)  # Good - async
```

But the Neo4j connector uses synchronous driver:

```python
with self.driver.session(database=database) as session:  # Blocking I/O
```

**Recommendation**: Use async Neo4j driver:
```python
from neo4j import AsyncGraphDatabase
```

---

### 🟢 Minor Improvements

#### 4.7 Prompt Template Hardcoding
**File**: `extraction/linker.py:38`

```python
template_str = self.prompt_manager.cfg.linking_prompt.linking
```

This tightly couples the linker to specific config structure.

**Recommendation**: Add dedicated method to `PromptManager`:
```python
def render_linking_prompt(self, context: dict) -> str:
    ...
```

#### 4.8 Missing Health Checks
**File**: `docker-compose.yml`

Only `elasticsearch` has a health check. Add to:
- `neo4j`
- `datahub-gms`
- `extraction-service`

#### 4.9 Duplicate Agent Definitions
**File**: `extraction/agent_server.py:122-126`

```python
# --- Agents ---
# --- Agents ---
# --- Agents ---
```

Cleanup duplicated comments.

---

## 5. Feature Gap Analysis vs. `feature-kgbuild`

The `feature-kgbuild` branch contains valuable components not yet in `graphrag-dev`:

| Feature | Status in graphrag-dev | Exists in feature-kgbuild |
|---------|----------------------|---------------------------|
| Evaluation Framework | ❌ | ✅ `tests/integration/test_agent_evaluation.py` |
| Retrieval Metrics | ❌ | ✅ `src/utils/retrieval_metrics.py` |
| Experiment Tracking | ❌ | ✅ `src/utils/experiment_metrics.py` |
| RDF Tools | ❌ | ✅ `src/retrieval/rdf_tools.py` |
| Modular CLI | ❌ | ✅ Restructured `src/` |

**Recommendation**: Cherry-pick evaluation framework from `feature-kgbuild`.

---

## 6. Proposed Architecture Improvements

### 6.1 Add Caching Layer

```
┌──────────┐     ┌─────────┐     ┌────────┐
│  Agent   │────▶│  Redis  │────▶│ Neo4j  │
│  Server  │     │ (Cache) │     │        │
└──────────┘     └─────────┘     └────────┘
```

Cache frequent Cypher query results to reduce Neo4j load.

### 6.2 Implement Circuit Breaker

For external dependencies (OpenAI, DataHub GMS):

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
def call_openai(self, ...):
    ...
```

### 6.3 Add Structured Logging

Replace `print()` statements with structured logging:

```python
import structlog
logger = structlog.get_logger()

logger.info("pipeline_step", step="extraction", item_id=item['id'], node_count=len(nodes))
```

### 6.4 Implement Rate Limiting

Add rate limiting to FastAPI endpoints:

```python
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)

@app.post("/run_agent")
@limiter.limit("10/minute")
async def run_agent(request: QueryRequest):
    ...
```

---

## 7. Priority Action Items

| Priority | Action | Effort | Impact |
|----------|--------|--------|--------|
| 🔴 P0 | Fix Cypher injection vulnerability | Low | High |
| 🔴 P0 | Initialize faiss_manager/neo4j_conn in agent_server | Low | High |
| 🟡 P1 | Add health checks to docker-compose | Low | Medium |
| 🟡 P1 | Externalize hardcoded credentials | Low | Medium |
| 🟡 P1 | Improve test coverage | Medium | High |
| 🟢 P2 | Migrate to async Neo4j driver | Medium | Medium |
| 🟢 P2 | Cherry-pick evaluation framework | Medium | High |
| 🟢 P2 | Add structured logging | Medium | Medium |

---

## 8. Conclusion

The `graphrag-dev` branch represents a well-architected enterprise GraphRAG system with:
- Clean multi-agent hierarchy
- Proper separation of concerns
- Strong observability features

Key areas for immediate attention:
1. **Security hardening** (Cypher injection, credential management)
2. **Runtime stability** (missing global initializations)
3. **Testing** (expand beyond smoke tests)

The codebase is production-ready with the critical fixes applied.

---

*Generated by Warp Agent Analysis*
