# SEOCHO Architecture — Agent-Driven Development Platform

## Overview

SEOCHO는 비정형 데이터를 Knowledge Graph로 변환하고, 동적으로 생성되는 DB별 Agent Pool이 Parallel Debate 패턴으로 질의에 응답하는 플랫폼입니다.

Current baseline:

- Agent runtime: OpenAI Agents SDK
- Trace/evaluation: Opik
- Graph backend: DozerDB (Neo4j protocol compatible)

## Control Plane vs Data Plane

### Control Plane

- agent orchestration and routing policies
- runtime authorization policy enforcement
- deployment quality gates and ADR governance

Primary modules:

- `extraction/agent_server.py`
- `extraction/policy.py`
- `docs/decisions/`

### Data Plane

- data ingestion and extraction pipeline
- SHACL-like rule inference and validation
- graph loading/query execution on DozerDB

Primary modules:

- `extraction/pipeline.py`
- `extraction/rule_constraints.py`
- `extraction/graph_loader.py`

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
  RuleConstraints (SHACL-like inference + validation)
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

## Query-Time Semantic Flow (New)

For graph QA with hard entity disambiguation requirements:

```
User Question
    │
    ▼
Semantic Layer
- extract question entities
- fulltext lookup on DozerDB/Neo4j
- semantic dedup/disambiguation with label hints
    │
    ▼
RouterAgent
    ├── LPGAgent (property graph neighborhood path)
    └── RDFAgent (RDF/ontology-oriented path)
             │
             ▼
AnswerGenerationAgent (final synthesis)
```

Why this path exists:

- query-time entity mapping is the hardest failure point in graph QA
- fulltext-first lookup improves recall for imperfect user entity strings
- semantic re-ranking + dedup reduces wrong-node selection before Cypher generation

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
| RuleConstraints | `extraction/rule_constraints.py` | SHACL-like 규칙 추론 및 노드 제약 검증 어노테이션 |
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

### Observability Layer

| Module | File | Purpose |
|--------|------|---------|
| Tracing | `extraction/tracing.py` | Opik integration: `configure_opik()`, `wrap_openai_client()`, `@track`, `update_current_span` |
| Config | `extraction/config.py` | `OPIK_URL`, `OPIK_WORKSPACE`, `OPIK_PROJECT_NAME`, `OPIK_ENABLED` |

### UI Layer

| Module | File | Purpose |
|--------|------|---------|
| Agent Studio | `evaluation/app.py` | Streamlit split-screen (chat + live trace graph) — **PoC demo용** |

## Three Execution Modes

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

### 3. Semantic Graph QA Mode (`POST /run_agent_semantic`)

```
User → SemanticLayer → RouterAgent → {LPGAgent | RDFAgent | both} → AnswerGenerationAgent
```

- 4-agent model aligned with current query-time disambiguation requirement
- semantic layer performs:
  - question entity extraction
  - fulltext index candidate search
  - contains-based fallback search
  - optional ontology-hint alias/label boost from offline artifact (`output/ontology_hints.json`)
  - lightweight semantic dedup/disambiguation
- frontend can pin candidate mappings and resend as `entity_overrides`
- route policy:
  - RDF hints (`rdf`, `owl`, `shacl`, `sparql`) → RDFAgent
  - default → LPGAgent
  - mixed hints → hybrid path (both agents)

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

## Observability: Agent Studio vs Opik

두 시스템의 역할이 명확히 분리되어 있습니다:

### Agent Studio (PoC Demo)
- **목적**: 이해관계자 데모, PoC 시연, 실시간 agent flow 시각화
- **위치**: `evaluation/app.py` (Streamlit, port 8501)
- **trace 방식**: `_build_debate_trace()`로 수동 구성된 FANOUT/DEBATE/COLLECT/SYNTHESIS 그래프
- **제한**: LLM 비용/latency 추적 없음, evaluation 기능 없음

### Opik (Production Eval & Trace)
- **목적**: 개발/디버깅/운영 모니터링, LLM evaluation, agent visualization
- **위치**: `http://localhost:5173` (opt-in: `docker compose --profile opik up -d`)
- **trace 방식**: `@track` 데코레이터 + `wrap_openai_client` 자동 tracing → native span tree
- **기능**: parent-child span tree, LLM 비용/latency, datasets & experiments, scoring

### Opik Span Tree (Debate Pattern)

Opik에서 Debate 패턴은 다음과 같은 span hierarchy로 표현됩니다:

```
agent_server.run_debate                          [tags: debate-mode]
  └─ debate.run_debate                           [phase: orchestration, agent_count: N]
       ├─ debate.run_single_agent                [phase: fan-out, db: kgnormal]
       │    └─ (OpenAI chat.completions.create)  [auto-traced]
       ├─ debate.run_single_agent                [phase: fan-out, db: kgfibo]
       │    └─ (OpenAI chat.completions.create)  [auto-traced]
       └─ debate.supervisor_synthesis            [phase: synthesis]
            └─ (OpenAI chat.completions.create)  [auto-traced]
```

각 span에 `metadata` (db_name, agent_name, phase)와 `tags`가 첨부되어 Opik UI에서 필터링/검색 가능.

### Streamlit Trace Topology (PoC Demo)

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

# Opik (opt-in)
OPIK_VERSION=latest
OPIK_URL=http://opik-backend:8080/api
OPIK_PROJECT_NAME=seocho
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
| `/run_agent_semantic` | POST | Semantic entity-resolution flow (Router/LPG/RDF/Answer) |
| `/run_debate` | POST | Parallel debate mode |
| `/indexes/fulltext/ensure` | POST | Ensure fulltext index exists for semantic routing |
| `/databases` | GET | List registered databases |
| `/agents` | GET | List active DB-bound agents |

Offline helper:

- `scripts/ontology/build_ontology_hints.py` builds `output/ontology_hints.json` from OWL via owlready2.
