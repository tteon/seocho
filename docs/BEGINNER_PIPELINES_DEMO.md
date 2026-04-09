# Beginner Pipeline Demos

Use this document after [QUICKSTART.md](QUICKSTART.md) succeeds.

This is the scripted demo path.
Unlike `QUICKSTART`, it is not the shortest way to first success.
Unlike `TUTORIAL_FIRST_RUN`, it is not the manual API walkthrough.

## 1. What This Document Is For

These demos split SEOCHO into four staged pipelines:

1. raw data ingest
2. semantic artifact lifecycle
3. graph load and query
4. graph-backed chat and Opik

Use it when you need:

- a repeatable demo for teammates
- script outputs you can inspect later
- a staged explanation of the product

## 2. Before You Start

Complete [QUICKSTART.md](QUICKSTART.md) first:

```bash
make setup-env
make up
```

Optional:

```bash
make opik-up
```

## 3. Run All Four Demos

```bash
scripts/demo/run_beginner_pipelines.sh --workspace default
```

If Opik is not running yet:

```bash
scripts/demo/run_beginner_pipelines.sh --workspace default --allow-no-opik
```

Default output directory:

```text
/tmp/seocho_beginner_demo
```

## 4. Run Demos Individually

### 4.1 Raw Data Pipeline

```bash
scripts/demo/pipeline_raw_data.sh --workspace default --db kgdemo_raw
```

What it verifies:

- `POST /platform/ingest/raw`
- raw record parsing and graph load

Output file:

- `01_raw_data_ingest.json`

### 4.2 Meta / Artifact Lifecycle Pipeline

```bash
scripts/demo/pipeline_meta_artifact.sh --workspace default --db kgdemo_meta
```

What it verifies:

- runtime ingest with `semantic_artifact_policy=draft_only`
- semantic artifact draft creation
- approval lifecycle

Output file:

- `02_meta_artifact_lifecycle.json`

### 4.3 Graph Load Pipeline

```bash
scripts/demo/pipeline_neo4j_load.sh --workspace default --db kgdemo_neo4j
```

What it verifies:

- runtime ingest
- graph persistence in DozerDB
- basic Cypher validation

Output file:

- `03_neo4j_load_and_query.json`

### 4.4 GraphRAG / Chat Pipeline

```bash
scripts/demo/pipeline_graphrag_opik.sh --workspace default --db kgdemo_graphrag
```

What it verifies:

- runtime ingest
- fulltext index bootstrap
- platform chat via evaluation proxy `POST /api/chat/send`
- semantic or debate response flow
- optional Opik visibility

Output file:

- `04_graphrag_with_opik.json`

## 5. Makefile Shortcuts

```bash
make demo-raw
make demo-meta
make demo-neo4j
make demo-graphrag-opik
make demo-all
```

## 6. Recommended Demo Order

1. raw data
2. artifact lifecycle
3. graph load
4. graph chat and Opik

That order explains the product from source material to governed graph memory to retrieval.

## 7. When To Use Which Doc

- [QUICKSTART.md](QUICKSTART.md): fastest first run
- [TUTORIAL_FIRST_RUN.md](TUTORIAL_FIRST_RUN.md): manual API walkthrough
- this document: scripted staged demos
