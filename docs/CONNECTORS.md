# Connectors

SEOCHO connectors make external ecosystem data easy to bring into the existing
index -> query -> report path.

The first connector contract is deliberately simple:

1. read from an external source
2. normalize each item into a SEOCHO connector record
3. write JSONL
4. point `seocho.run.yaml` at that JSONL with `documents.path`

Connectors do not bypass ontology extraction, validation, graph shaping, or
evidence reporting. They only materialize source data into a stable input
format that SEOCHO already knows how to index.

## CLI

```bash
seocho connect notion --data-source-id "$NOTION_DATA_SOURCE_ID" \
  --token-env NOTION_TOKEN \
  --output .seocho/connectors/notion.jsonl

seocho connect slack --channel "$SLACK_CHANNEL_ID" \
  --token-env SLACK_BOT_TOKEN \
  --team-id "$SLACK_TEAM_ID" \
  --output .seocho/connectors/slack.jsonl

seocho connect datahub --server "$DATAHUB_SERVER" \
  --token-env DATAHUB_TOKEN \
  --query "*" \
  --output .seocho/connectors/datahub.jsonl

seocho connect postgres --dsn-env DATABASE_URL \
  --schema public \
  --database-name app \
  --output .seocho/connectors/postgres.jsonl

seocho connect neo4j \
  --database neo4j \
  --output .seocho/connectors/neo4j.jsonl
```

Then use the generated file as your run input:

```yaml
ontology: ./schema.yaml
documents:
  path: ./.seocho/connectors/notion.jsonl
questions:
  - What decisions are documented in our workspace?
```

From a repo checkout, prefix commands with `uv run`.

`seocho connectors ...` is accepted as an alias for `seocho connect ...`.

## Provider Boundaries

Connectors bring source data into SEOCHO. Model providers stay in run specs and
SDK constructors:

- OpenAI, MARA, DeepSeek, Kimi, and similar providers are LLM/embedding choices
  under `models` or `llm=...`
- JSON-LD / YAML / TTL files are ontology inputs under `ontology.path`
- PostgreSQL, Neo4j/DozerDB, DataHub, Notion, Slack, LangChain, and LlamaIndex
  are source materialization paths

## Python

Use LangChain or LlamaIndex loaders exactly where they already work, then
convert their document objects into SEOCHO records:

```python
from seocho.connectors import records_from_langchain_documents, write_records_jsonl

records = records_from_langchain_documents(
    loader.lazy_load(),
    category="contracts",
)
write_records_jsonl(records, ".seocho/connectors/contracts.jsonl")
```

No LangChain or LlamaIndex dependency is imported by SEOCHO. The converter uses
duck typing: `page_content` / `metadata` / `id` for LangChain-like objects and
`get_content()` or `text` / `metadata` / `id_` for LlamaIndex-like objects.

## Record Shape

Each line is JSON:

```json
{
  "id": "notion:page-id",
  "content": "Rendered source text...",
  "category": "notion",
  "source_type": "text",
  "metadata": {
    "provider": "notion",
    "source_kind": "notion_page",
    "schema_version": "seocho.connector_record.v1",
    "content_sha256": "..."
  }
}
```

Provider-specific identifiers, page titles, timestamps, field lists, channel
IDs, and framework metadata are preserved in `metadata`. Obvious credential
fields such as `token`, `secret`, `password`, `authorization`, and `cookie` are
redacted before JSONL is written.

## Provider Notes

Notion:

- uses `Notion-Version: 2026-03-11` by default
- reads pages and data-source rows
- recursively renders block children into Markdown-like text
- stores only `token_env` in commands and configs; export the token separately

Slack:

- reads channel history by channel ID
- can group replied messages as thread records with `--threads`
- starts with bot/user tokens from env vars
- defaults to `--limit 15` to match Slack's current non-Marketplace commercial
  app cap; internal or Marketplace apps can raise this, commonly to `--limit 200`
- does not enable DMs, private channels, file downloads, or email enrichment by
  default

DataHub:

- reads dataset metadata through GraphQL search
- preserves schema fields, owners, tags, and glossary terms when present
- remains distinct from `seocho ontology datahub`, which exports SEOCHO ontology
  governance artifacts to DataHub

PostgreSQL:

- reads `information_schema.columns`
- defaults to schema metadata only, not raw row sampling
- requires `seocho[postgres]` or compatible `psycopg` installation
- use a least-privilege read-only DSN and avoid logging DSNs

Neo4j / DozerDB:

- reads schema procedure output through the Neo4j driver
- materializes node labels, relationship types, and observed properties
- defaults to metadata only, not raw graph export
- reads `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` by env-var name

## Live Evidence

Offline tests validate record conversion and JSONL ingestion. Live connector
claims require real runs against the named services with versions, dataset
scope, limits, and skipped components reported. Do not use mocked connector
tests as evidence for throughput, latency, or external compatibility.
