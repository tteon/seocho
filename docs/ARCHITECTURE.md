# SEOCHO Architecture — Agent-Driven Development Platform

## Overview

SEOCHO는 비정형 데이터를 Knowledge Graph로 변환하고, 동적으로 생성되는 DB별 Agent Pool이 Parallel Debate 패턴으로 질의에 응답하는 플랫폼입니다.

## End-to-End Data Flow

```
[External Data]
       │
       ▼
  DataSource (CSV/JSON/Parquet/API)
       │
       ▼
  OntologyPromptBridge (ontology → LLM prompt injection)
       │
       ▼
  EntityExtractor (LLM-based entity/relationship extraction)
       │
       ▼
  EntityLinker (LLM-based entity resolution)
       │
       ▼
  EntityDeduplicator (embedding similarity dedup)
       │
       ▼
  DatabaseManager (Neo4j DB creation + schema + load)
       │
       ▼
  AgentFactory (DB별 전용 Agent 생성)
       │
       ▼
  User Question → Router/DebateOrchestrator → AgentPool → SharedMemory → Supervisor → Answer
```

## Module Map

### Data Ingestion Layer

| Module | File | Purpose |
|--------|------|---------|
| DataSource | `extraction/data_source.py` | ABC + FileDataSource(CSV/JSON/Parquet) + APIDataSource |
| DataCollector | `extraction/collector.py` | Legacy HuggingFace collector (backward compat) |

**Standard record format**: `{"id": str, "content": str, "category": str, "source": str, "metadata": dict}`

### Extraction Layer

| Module | File | Purpose |
|--------|------|---------|
| OntologyPromptBridge | `extraction/ontology_prompt_bridge.py` | Ontology → LLM prompt variable 변환 |
| EntityExtractor | `extraction/extractor.py` | OpenAI LLM 기반 entity/relationship extraction |
| EntityLinker | `extraction/linker.py` | LLM 기반 entity resolution |
| EntityDeduplicator | `extraction/deduplicator.py` | Embedding cosine similarity 기반 semantic dedup |
| PromptManager | `extraction/prompt_manager.py` | Jinja2 prompt templating + history logging |

### Database Layer

| Module | File | Purpose |
|--------|------|---------|
| DatabaseRegistry | `extraction/config.py` | Runtime-extensible DB name allowlist (singleton: `db_registry`) |
| DatabaseManager | `extraction/database_manager.py` | DB provisioning + schema + data loading |
| GraphLoader | `extraction/graph_loader.py` | Neo4j MERGE operations (label-validated) |
| SchemaManager | `extraction/schema_manager.py` | Constraint/index application |

### Agent Layer

| Module | File | Purpose |
|--------|------|---------|
| AgentFactory | `extraction/agent_factory.py` | DB별 전용 Agent 동적 생성 |
| SharedMemory | `extraction/shared_memory.py` | 요청 단위 agent 간 공유 메모리 + query cache |
| DebateOrchestrator | `extraction/debate.py` | Parallel Debate 패턴 (fan-out → collect → synthesize) |
| Agent Server | `extraction/agent_server.py` | FastAPI endpoints (`/run_agent`, `/run_debate`) |

### UI Layer

| Module | File | Purpose |
|--------|------|---------|
| Agent Studio | `evaluation/app.py` | Streamlit split-screen (chat + live trace graph) |

## Two Execution Modes

### 1. Legacy Router Mode (`POST /run_agent`)

```
User → Router → {GraphAgent, VectorAgent, WebAgent, TableAgent} → Supervisor → Answer
```

- 기존 정적 7-agent 파이프라인
- Router가 1개의 specialist에 라우팅
- Sequential handoff chain

### 2. Parallel Debate Mode (`POST /run_debate`)

```
User → DebateOrchestrator → [Agent_db1 ∥ Agent_db2 ∥ ... ∥ Agent_dbN] → SharedMemory → Supervisor → Answer
```

- 모든 DB agent가 `asyncio.gather()`로 병렬 실행
- 각 agent 결과가 SharedMemory에 저장
- Supervisor가 모든 결과를 합성
- 에러 격리: 1개 agent 실패해도 나머지 결과로 합성

## Key Patterns

### DatabaseRegistry (Global Singleton)

```python
from config import db_registry

db_registry.register("mydb01")         # 등록
db_registry.is_valid("mydb01")         # 검증 (True)
db_registry.list_databases()           # 사용자 DB 목록 (system/neo4j 제외)
```

- DB명 validation: `^[A-Za-z][A-Za-z0-9]*$` (영문 시작, 영숫자만)
- `VALID_DATABASES` (legacy set)는 `db_registry._databases`를 참조

### AgentFactory (Closure-bound Tools)

```python
factory = AgentFactory(neo4j_connector)
agent = factory.create_db_agent("kgnormal", schema_info)
```

- 각 agent의 `query_db` tool은 closure로 특정 DB에 바인딩
- SharedMemory 캐시 자동 통합 (RunContextWrapper를 통해)

### SharedMemory (Request-scoped)

```python
memory = SharedMemory()
memory.cache_query_result("kgnormal", "MATCH (n) RETURN n", "[...]")
memory.get_cached_query("kgnormal", "MATCH (n) RETURN n")  # cache hit
memory.put("agent_result:kgnormal", "answer text")
memory.get_all_results()  # Supervisor용 전체 결과
```

- 요청 당 1개 인스턴스
- Cypher query MD5 해시 기반 캐싱

### OntologyPromptBridge

```python
from ontology.base import Ontology
from ontology_prompt_bridge import OntologyPromptBridge

ontology = Ontology.from_yaml("conf/schemas/fibo.yaml")
bridge = OntologyPromptBridge(ontology)
context = bridge.render_extraction_context()
# → {"entity_types": "- Organization: ...", "relationship_types": "...", "ontology_name": "FIBO"}
```

- 온톨로지 YAML의 NodeDefinition/RelationshipDefinition을 LLM 프롬프트 변수로 변환
- `default.yaml` 프롬프트에서 `{% if ontology_name %}` 분기로 동적 vs 레거시 프롬프트

## Trace Visualization (Streamlit)

### Debate Trace Topology

```
FANOUT (yellow) ─┬─ DEBATE: Agent_kgnormal (blue)
                 ├─ DEBATE: Agent_kgfibo   (blue)
                 └─ DEBATE: Agent_xxx      (blue)
                          │
                 COLLECT (orange) ← 모든 DEBATE
                          │
                 SYNTHESIS: Supervisor (green)
```

Edge routing은 `metadata.parent` (fan-out) 및 `metadata.sources` (collect) 기반.

## Configuration

### Environment Variables (`.env`)
```
OPENAI_API_KEY=sk-...
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
```

### Hydra Config (`extraction/conf/`)
```
conf/
├── config.yaml          # Global: model, mock_data, openai_api_key
├── prompts/
│   ├── default.yaml     # Extraction prompt (supports ontology variables)
│   ├── linking.yaml     # Entity linking prompt
│   └── router.yaml      # Router agent prompt
└── schemas/
    ├── baseline.yaml    # kgnormal schema
    ├── fibo.yaml        # kgfibo schema
    └── tracing.yaml     # agent_traces schema
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/run_agent` | POST | Legacy router mode |
| `/run_debate` | POST | Parallel debate mode |
| `/databases` | GET | List registered databases |
| `/agents` | GET | List active DB-bound agents |
