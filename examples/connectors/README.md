# Connector Starting Point

This directory is the shortest path from "my data lives somewhere else" to a
SEOCHO-ready JSONL file.

Use it for two jobs:

1. Try the connector record shape without any live SaaS credentials.
2. Copy the live connector config pattern for Notion, Slack, DataHub,
   PostgreSQL, and Neo4j/DozerDB.

## Run The No-Service Starter

From the repository root:

```bash
uv run python examples/connectors/custom_loader.py \
  --input examples/connectors/fixtures/custom_saas_events.json \
  --output .seocho/connectors/custom_saas.jsonl

uv run seocho run examples/connectors/run.yaml --show-rendered
```

The script reads a small local fixture and writes
`seocho.connector_record.v1` records. Replace the fixture reader with your own
API client while keeping the `ConnectorRecord` output shape.

When you have a local graph extra and a model key configured, run the normal
preflight too:

```bash
export MARA_API_KEY=...
uv run seocho run examples/connectors/run.yaml --dry-run
```

## Try The Live Config

Copy the starter config to your project and fill in only the sources you need:

```bash
cp examples/connectors/seocho.connectors.example.yaml seocho.connectors.yaml
uv run seocho connect run seocho.connectors.yaml --dry-run
uv run seocho connect run seocho.connectors.yaml
```

Live sources read credentials from environment variables such as
`NOTION_TOKEN`, `SLACK_BOT_TOKEN`, `DATAHUB_TOKEN`, `DATABASE_URL`,
`NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD`.

## Recommended Next Connectors

These are the connector families that best fit SEOCHO's ontology-aligned graph
memory layer.

| Connector | Why it fits SEOCHO | First record kinds |
|---|---|---|
| GitHub | Issues, PRs, discussions, reviews, and commits are high-signal engineering evidence. | `github_issue`, `github_pull_request`, `github_review`, `github_commit` |
| Google Drive / Docs | Many teams keep policies, proposals, meeting notes, and customer docs there. | `gdrive_file`, `google_doc`, `google_sheet` |
| Confluence / Jira | Enterprise knowledge and work tracking often live together here. | `confluence_page`, `jira_issue`, `jira_comment` |
| Linear | Modern product teams use Linear issues and project docs as planning evidence. | `linear_issue`, `linear_project`, `linear_comment` |
| S3 / GCS / Azure Blob | Data lakes and export buckets are the easiest way to ingest broad document corpora. | `object_file`, `object_manifest`, `object_metadata` |
| Snowflake / BigQuery / dbt | Warehouse schemas and lineage explain what operational data means. | `warehouse_table`, `warehouse_column`, `dbt_model`, `dbt_lineage` |
| Zendesk / Intercom | Support tickets show user pain, product language, and unresolved operational risk. | `support_ticket`, `support_conversation`, `support_article` |
| Microsoft Teams / Discord | Slack is covered; the same message/thread model should extend to other team chats. | `chat_message`, `chat_thread`, `chat_channel` |
| Webhook / Kafka | Event streams can feed live memories when teams need fresh operational state. | `event_message`, `event_batch`, `event_schema` |

Build them in this order if you want the biggest open-source payoff:

1. GitHub, because every developer can test it against a public repository.
2. Google Drive or Confluence, because document ingestion is the common first
   production ask.
3. Snowflake, BigQuery, or dbt, because schema and lineage are where graph RAG
   becomes more than text search.
4. Zendesk or Intercom, because user-support evidence makes the value obvious
   to product and customer teams.

## Connector Contract

Keep custom connectors boring:

- `check`: fail early when credentials, scopes, or source identifiers are wrong
- `discover`: list available streams, schemas, or source kinds
- `read`: materialize source records into SEOCHO JSONL
- `state`: write content-free cursors, record IDs, and fingerprints

SEOCHO does not need a heavy connector runtime to start. It needs repeatable
materialization into records that the normal index -> query -> report flow can
already consume.
