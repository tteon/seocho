# Beginner 4-Pipeline Demo

초보자 온보딩을 위해 SEOCHO를 아래 4개 파이프라인으로 분리해 데모할 수 있습니다.

1. Raw Data Pipeline
2. Meta Pipeline (semantic artifact lifecycle)
3. Neo4j Pipeline (load + Cypher query)
4. GraphRAG Pipeline (semantic/debate + Opik)

## 1. Prerequisites

- Docker / Docker Compose
- `curl`, `jq`
- (Neo4j pipeline) `docker exec` 사용 가능 환경
- (GraphRAG strict mode) 유효한 `OPENAI_API_KEY`
- (Opik) `make opik-up` + `.env`의 `OPIK_URL` 설정

예시:

```bash
cp .env.example .env
# .env 편집: OPENAI_API_KEY, (선택) OPIK_URL=http://opik-backend:8080
```

서비스 기동:

```bash
make up
# Opik 포함 기동 (선택)
make opik-up
```

## 2. Quick Start (All Pipelines)

아래 한 줄로 4개 파이프라인을 순차 실행합니다.

```bash
scripts/demo/run_beginner_pipelines.sh --workspace default
```

Opik이 아직 없으면 임시로:

```bash
scripts/demo/run_beginner_pipelines.sh --workspace default --allow-no-opik
```

결과 JSON은 기본적으로 `/tmp/seocho_beginner_demo`에 저장됩니다.

## 3. Run Pipelines Individually

### 3.1 Raw Data

```bash
scripts/demo/pipeline_raw_data.sh --workspace default --db kgdemo_raw
```

- Endpoint: `POST /platform/ingest/raw`
- Output: `01_raw_data_ingest.json`

### 3.2 Meta

```bash
scripts/demo/pipeline_meta_artifact.sh --workspace default --db kgdemo_meta
```

- Endpoints:
  - `POST /platform/ingest/raw` (`semantic_artifact_policy=draft_only`)
  - `POST /semantic/artifacts/drafts`
  - `POST /semantic/artifacts/{artifact_id}/approve`
- Output: `02_meta_artifact_lifecycle.json`

### 3.3 Neo4j

```bash
scripts/demo/pipeline_neo4j_load.sh --workspace default --db kgdemo_neo4j
```

- Endpoint: `POST /platform/ingest/raw`
- Validation: `docker exec graphrag-neo4j cypher-shell ...`
- Output: `03_neo4j_load_and_query.json`

### 3.4 GraphRAG with Opik

```bash
scripts/demo/pipeline_graphrag_opik.sh --workspace default --db kgdemo_graphrag
```

- Endpoints:
  - `POST /platform/ingest/raw`
  - `POST /indexes/fulltext/ensure`
  - `POST /api/chat/send` (semantic/debate)
- Opik checks:
  - `opik-backend` container running
  - `http://localhost:5173` reachable
  - extraction-service `OPIK_URL_OVERRIDE` configured
- Output: `04_graphrag_with_opik.json`

## 4. Makefile Shortcuts

```bash
make demo-raw
make demo-meta
make demo-neo4j
make demo-graphrag-opik
make demo-all
```

`demo-graphrag-opik`는 Opik 미기동 환경에서 실패할 수 있습니다.
필요하면 `scripts/demo/pipeline_graphrag_opik.sh --allow-no-opik`를 사용하세요.

## 5. Recommended Live Demo Order

1. `pipeline_raw_data.sh`로 "원천 데이터가 들어온다"를 먼저 보여줍니다.
2. `pipeline_meta_artifact.sh`로 메타/거버넌스(draft -> approved)를 시연합니다.
3. `pipeline_neo4j_load.sh`로 실제 graph DB 적재 확인(Cypher)을 시연합니다.
4. `pipeline_graphrag_opik.sh`로 질의응답과 Opik trace를 보여줍니다.

Opik UI: <http://localhost:5173>
