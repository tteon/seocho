# Files And Artifacts

This page answers the practical question: after I run SEOCHO, where do the
important files live, and how do I inspect them directly?

## 1. Ontology Files

Typical paths:

- `schema.jsonld`
  - default ontology file used by CLI examples
- any custom path you pass with `--schema`
- sample ontology files in `examples/datasets/*.jsonld`

How to inspect:

```bash
cat schema.jsonld | jq .
seocho ontology check --schema schema.jsonld
seocho ontology export --schema schema.jsonld --format shacl --output shacl.json
seocho ontology diff --left schema_v1.jsonld --right schema_v2.jsonld
```

What they are for:

- define node types, relationships, properties, and cardinality
- drive extraction prompts, validation, and query generation
- act as the portable schema contract you can version in git

## 2. Local Graph Data

Typical path:

- `data/neo4j/`

Important subdirectories:

- `data/neo4j/data`
- `data/neo4j/logs`
- `data/neo4j/import`
- `data/neo4j/plugins`

How to inspect:

- DozerDB browser: `http://localhost:7474`
- Bolt endpoint: `bolt://localhost:7687`
- Python local mode:

```python
from seocho.store import Neo4jGraphStore

store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")
print(store.get_schema(database="neo4j"))
```

## 3. Semantic Artifacts

Typical path:

- `outputs/semantic_artifacts/`

What lives there:

- draft and approved ontology candidates
- SHACL-like shape candidates
- vocabulary candidates used for governed semantic retrieval

How to inspect:

```bash
seocho artifacts list --status approved
curl -sS "http://localhost:8001/semantic/artifacts?workspace_id=default" | jq .
curl -sS "http://localhost:8001/semantic/artifacts/<ARTIFACT_ID>?workspace_id=default" | jq .
```

## 4. Rule Profiles

Typical path:

- `outputs/rule_profiles/rule_profiles.db`

What lives there:

- workspace-scoped rule profiles derived from runtime or governance flows

How to inspect:

```bash
curl -sS "http://localhost:8001/rules/profiles?workspace_id=default" | jq .
```

If you want to inspect the SQLite file directly:

```bash
sqlite3 outputs/rule_profiles/rule_profiles.db '.tables'
```

## 5. Semantic Run Metadata

Typical path:

- `outputs/semantic_metadata/`

What lives there:

- semantic run summaries
- route / support / strategy metadata
- response previews used for debugging and regression review

How to inspect:

```bash
curl -sS "http://localhost:8001/semantic/runs?workspace_id=default&limit=5" | jq .
```

## 6. Trace Artifacts

Typical path:

- `traces/seocho-runtime.jsonl`
- or whatever `SEOCHO_TRACE_JSONL_PATH` points to

How to inspect:

```bash
tail -n 20 traces/seocho-runtime.jsonl
```

Recommended interpretation:

- `jsonl` is the neutral, portable trace artifact
- Opik is optional and can be hosted or self-hosted

## 7. Examples And Sample Data

Useful starting points:

- `examples/quickstart.ipynb`
- `examples/bring_your_data.ipynb`
- `examples/datasets/finder_sample.json`
- `examples/datasets/fibo_base.jsonld`
- `examples/datasets/fibo_plus.jsonld`
- `examples/datasets/fibo_minus.jsonld`

## 8. Compose Runtime Shape

Default `make up` or `docker compose up -d` starts:

- `neo4j`
- `extraction-service`
- `evaluation-interface`

Legacy service kept behind an explicit profile:

```bash
docker compose --profile legacy-semantic up -d semantic-service
```

That means the current first-run product path is:

1. store graph state in `data/neo4j/`
2. talk to the runtime through `extraction-service`
3. optionally use the thin local UI in `evaluation-interface`
