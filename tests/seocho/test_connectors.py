from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass

from seocho.connectors import (
    ConnectorRecord,
    load_connector_config,
    read_records_jsonl,
    records_from_langchain_documents,
    records_from_llamaindex_documents,
    run_connector_plan,
    write_sample_config,
    write_records_jsonl,
)
from seocho.connectors import config as connector_config
from seocho.connectors.config import ConnectorConfigError
from seocho.connectors.datahub import DataHubGraphQLClient, dataset_entity_to_record
from seocho.connectors.notion import NotionClient, blocks_to_markdown, page_to_record
from seocho.connectors.neo4j import records_from_schema_rows as records_from_neo4j_schema_rows
from seocho.connectors.postgres import records_from_schema_rows
from seocho.connectors.records import sanitize_metadata
from seocho.connectors.slack import SlackClient, message_to_record, thread_to_record
from seocho.cli import build_parser, main
from seocho.index.file_reader import read_jsonl_file


@dataclass
class _LangChainDoc:
    page_content: str
    metadata: dict
    id: str = ""


@dataclass
class _LlamaDoc:
    text: str
    metadata: dict
    id_: str = ""


class _MockResponse:
    def __init__(self, payload, *, status_code: int = 200, headers: dict | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _MockRequestSession:
    def __init__(self, responses: list[_MockResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


class _MockGetSession:
    def __init__(self, responses: list[_MockResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


class _MockPostSession:
    def __init__(self, responses: list[_MockResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def test_langchain_documents_convert_without_optional_dependency(tmp_path) -> None:
    records = records_from_langchain_documents(
        [
            _LangChainDoc(
                "ACME acquired Beta Analytics.",
                {"source": "loader://acme", "Authorization": "Bearer secret"},
                id="doc-1",
            )
        ],
        category="support",
    )

    assert len(records) == 1
    assert records[0].id == "langchain:doc-1"
    assert records[0].metadata["framework"] == "langchain"

    out = tmp_path / "records.jsonl"
    assert write_records_jsonl(records, out) == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["category"] == "support"
    assert payload["metadata"]["Authorization"] == "[redacted]"


def test_llamaindex_documents_convert_by_duck_typing() -> None:
    records = records_from_llamaindex_documents(
        [_LlamaDoc("A policy document.", {"title": "Policy"}, id_="node-7")]
    )

    assert records[0].id == "llamaindex:node-7"
    assert records[0].source_kind == "llamaindex_document"
    assert records[0].title == "Policy"


def test_connector_jsonl_round_trip_and_file_reader_metadata_merge(tmp_path) -> None:
    records = records_from_langchain_documents([
        _LangChainDoc("Jane Park is CEO of Acme.", {"source": "unit"}, id="jane")
    ])
    out = tmp_path / "records.jsonl"
    write_records_jsonl(records, out)

    roundtrip = list(read_records_jsonl(out))
    assert roundtrip[0].id == "langchain:jane"

    file_records = read_jsonl_file(out)
    assert file_records[0]["metadata"]["provider"] == "langchain"
    assert file_records[0]["metadata"]["schema_version"] == "seocho.connector_record.v1"
    assert file_records[0]["metadata"]["id"] == "langchain:jane"
    assert "metadata" not in file_records[0]["metadata"]


def test_notion_page_and_blocks_materialize_record() -> None:
    page = {
        "object": "page",
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "created_time": "2026-01-01T00:00:00.000Z",
        "last_edited_time": "2026-01-02T00:00:00.000Z",
        "properties": {
            "Name": {
                "type": "title",
                "title": [{"plain_text": "Engineering Wiki"}],
            },
            "Status": {"type": "status", "status": {"name": "Published"}},
        },
    }
    blocks = [
        {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Overview"}]}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Use SEOCHO."}]}},
    ]

    record = page_to_record(page, block_text=blocks_to_markdown(blocks))

    assert record.id == "notion:page-1"
    assert record.title == "Engineering Wiki"
    assert "## Overview" in record.content
    assert record.metadata["notion_properties"]["Status"] == "Published"


def test_notion_client_paginates_data_source_and_blocks() -> None:
    page_1 = {
        "object": "page",
        "id": "page-1",
        "properties": {"Name": {"type": "title", "title": [{"plain_text": "Page One"}]}},
    }
    page_2 = {
        "object": "page",
        "id": "page-2",
        "properties": {"Name": {"type": "title", "title": [{"plain_text": "Page Two"}]}},
    }
    session = _MockRequestSession([
        _MockResponse({"results": [page_1], "has_more": True, "next_cursor": "cursor-2"}),
        _MockResponse({"results": [page_2], "has_more": False}),
        _MockResponse({
            "results": [
                {
                    "id": "child",
                    "type": "heading_1",
                    "has_children": True,
                    "heading_1": {"rich_text": [{"plain_text": "Root"}]},
                }
            ],
            "has_more": False,
        }),
        _MockResponse({
            "results": [
                {
                    "id": "grandchild",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"plain_text": "Nested text"}]},
                }
            ],
            "has_more": False,
        }),
    ])
    client = NotionClient(token="secret", session=session)

    pages = list(client.iter_data_source_pages("ds-1", page_size=500))
    markdown = client.page_content_markdown("page-1")

    assert [page["id"] for page in pages] == ["page-1", "page-2"]
    assert session.calls[0]["json"]["page_size"] == 100
    assert session.calls[1]["json"]["start_cursor"] == "cursor-2"
    assert session.calls[2]["method"] == "GET"
    assert "# Root" in markdown
    assert "Nested text" in markdown


def test_slack_message_and_thread_materialize_records() -> None:
    msg = {
        "type": "message",
        "ts": "123.456",
        "thread_ts": "123.456",
        "user": "U123",
        "text": "Hello <@U456> in <#C123|general>",
        "reply_count": 1,
    }

    record = message_to_record(msg, channel_id="C123", team_id="T1", channel_name="general")
    assert record.id == "slack:T1:C123:123.456"
    assert "Hello @U456 in #general" in record.content

    thread = thread_to_record(
        [msg, {"type": "message", "ts": "124.000", "user": "U456", "text": "Reply"}],
        channel_id="C123",
        team_id="T1",
    )
    assert thread.source_kind == "slack_thread"
    assert thread.metadata["message_count"] == 2


def test_slack_client_paginates_history_and_retries_rate_limit(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("seocho.connectors.slack.time.sleep", lambda value: sleeps.append(value))
    session = _MockGetSession([
        _MockResponse({"ok": False}, status_code=429, headers={"Retry-After": "2"}),
        _MockResponse({
            "ok": True,
            "messages": [{"type": "message", "ts": "1", "text": "first"}],
            "response_metadata": {"next_cursor": "cursor-2"},
        }),
        _MockResponse({
            "ok": True,
            "messages": [{"type": "message", "ts": "2", "text": "second"}],
            "response_metadata": {"next_cursor": ""},
        }),
    ])
    client = SlackClient(token="xoxb-token", session=session)

    messages = list(client.iter_conversation_history("C123", limit=999))

    assert [message["ts"] for message in messages] == ["1", "2"]
    assert sleeps == [2.0]
    assert session.calls[0]["params"]["limit"] == 200
    assert session.calls[2]["params"]["cursor"] == "cursor-2"


def test_slack_fetch_channel_records_groups_threads(monkeypatch) -> None:
    class FakeSlackClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def iter_conversation_history(self, channel_id, **kwargs):
            assert channel_id == "C123"
            yield {
                "type": "message",
                "ts": "1",
                "thread_ts": "1",
                "text": "root",
                "user": "U1",
                "reply_count": 1,
            }

        def iter_conversation_replies(self, channel_id, thread_ts, **kwargs):
            assert (channel_id, thread_ts) == ("C123", "1")
            yield {"type": "message", "ts": "1", "thread_ts": "1", "text": "root", "user": "U1"}
            yield {"type": "message", "ts": "2", "thread_ts": "1", "text": "reply", "user": "U2"}

    monkeypatch.setattr("seocho.connectors.slack.SlackClient", FakeSlackClient)
    from seocho.connectors.slack import fetch_channel_records

    records = fetch_channel_records(["C123"], token_env="SLACK_TOKEN", include_threads=True)

    assert len(records) == 1
    assert records[0].source_kind == "slack_thread"
    assert "U2: reply" in records[0].content


def test_datahub_dataset_entity_materializes_schema_record() -> None:
    record = dataset_entity_to_record(
        {
            "urn": "urn:li:dataset:(urn:li:dataPlatform:postgres,db.public.orders,PROD)",
            "type": "DATASET",
            "name": "db.public.orders",
            "properties": {"description": "Orders table"},
            "schemaMetadata": {
                "fields": [
                    {"fieldPath": "order_id", "nativeDataType": "uuid", "description": "Primary key"}
                ]
            },
            "tags": {"tags": [{"tag": {"name": "pii"}}]},
            "glossaryTerms": {"terms": [{"term": {"name": "Order"}}]},
            "ownership": {"owners": [{"owner": {"urn": "urn:li:corpuser:ada"}}]},
        }
    )

    assert record.source_kind == "datahub_dataset"
    assert "order_id" in record.content
    assert record.metadata["field_count"] == 1
    assert record.metadata["tags"] == ["pii"]


def test_datahub_client_pages_search_and_sets_auth_header() -> None:
    session = _MockPostSession([
        _MockResponse({
            "data": {
                "search": {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:postgres,db.public.orders,PROD)",
                                "type": "DATASET",
                                "name": "db.public.orders",
                                "properties": {"description": "Orders table"},
                            }
                        }
                    ]
                }
            }
        }),
        _MockResponse({
            "data": {
                "search": {
                    "searchResults": [
                        {
                            "entity": {
                                "urn": "urn:li:dataset:(urn:li:dataPlatform:postgres,db.public.customers,PROD)",
                                "type": "DATASET",
                                "name": "db.public.customers",
                                "properties": {"description": "Customers table"},
                            }
                        }
                    ]
                }
            }
        }),
    ])
    client = DataHubGraphQLClient(server="https://datahub.example", token="dh-token", session=session)

    entities = list(client.iter_dataset_search(query_text="postgres", page_size=1, max_results=2))

    assert [entity["name"] for entity in entities] == ["db.public.orders", "db.public.customers"]
    assert session.calls[0]["url"] == "https://datahub.example/api/graphql"
    assert session.calls[0]["headers"]["Authorization"] == "Bearer dh-token"
    assert session.calls[0]["json"]["variables"] == {"query": "postgres", "start": 0, "count": 1}
    assert session.calls[1]["json"]["variables"] == {"query": "postgres", "start": 1, "count": 1}


def test_postgres_schema_rows_group_into_table_records() -> None:
    records = records_from_schema_rows(
        [
            {
                "table_schema": "public",
                "table_name": "orders",
                "column_name": "order_id",
                "ordinal_position": 1,
                "data_type": "uuid",
                "is_nullable": "NO",
            },
            {
                "table_schema": "public",
                "table_name": "orders",
                "column_name": "amount",
                "ordinal_position": 2,
                "data_type": "numeric",
                "is_nullable": "YES",
            },
        ],
        database="app",
    )

    assert len(records) == 1
    assert records[0].id == "postgres:postgres://app.public.orders"
    assert records[0].title == "app.public.orders"
    assert records[0].metadata["field_count"] == 2


def test_postgres_fetch_schema_records_uses_mock_psycopg(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params):
            calls["query"] = query
            calls["params"] = params

        def fetchall(self):
            return [
                {
                    "table_schema": "public",
                    "table_name": "orders",
                    "column_name": "order_id",
                    "ordinal_position": 1,
                    "data_type": "uuid",
                    "is_nullable": "NO",
                }
            ]

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return FakeCursor()

    def fake_connect(dsn, row_factory=None):
        calls["dsn"] = dsn
        calls["row_factory"] = row_factory
        return FakeConnection()

    fake_psycopg = types.SimpleNamespace(connect=fake_connect)
    fake_rows = types.SimpleNamespace(dict_row=object())
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", fake_rows)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://example")
    from seocho.connectors.postgres import fetch_schema_records

    records = fetch_schema_records(dsn_env="TEST_DATABASE_URL", schemas=["public"], database="app")

    assert calls["dsn"] == "postgresql://example"
    assert calls["params"] == [["public"]]
    assert "table_schema = any(%s)" in str(calls["query"])
    assert records[0].id == "postgres:postgres://app.public.orders"


def test_neo4j_schema_rows_materialize_schema_record() -> None:
    records = records_from_neo4j_schema_rows(
        node_rows=[
            {
                "nodeType": ":`Company`",
                "propertyName": "name",
                "propertyTypes": ["String"],
                "mandatory": True,
            }
        ],
        relationship_rows=[
            {
                "relType": ":`WORKS_AT`",
                "propertyName": "since",
                "propertyTypes": ["Integer"],
                "mandatory": False,
            }
        ],
        database="neo4j",
    )

    assert len(records) == 1
    assert records[0].id == "neo4j:neo4j://neo4j/schema"
    assert records[0].source_kind == "neo4j_schema"
    assert ":`Company`" in records[0].content
    assert records[0].metadata["node_types"] == [":`Company`"]
    assert records[0].metadata["relationship_types"] == [":`WORKS_AT`"]


def test_neo4j_fetch_schema_records_uses_mock_driver(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeSession:
        def __init__(self, database) -> None:
            calls["database"] = database

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def run(self, query):
            calls.setdefault("queries", []).append(query)
            if "nodeTypeProperties" in query:
                return [
                    {
                        "nodeType": ":`Company`",
                        "propertyName": "name",
                        "propertyTypes": ["String"],
                        "mandatory": True,
                    }
                ]
            return [
                {
                    "relType": ":`WORKS_AT`",
                    "propertyName": "since",
                    "propertyTypes": ["Integer"],
                    "mandatory": False,
                }
            ]

    class FakeDriver:
        def session(self, database=None):
            return FakeSession(database)

        def close(self):
            calls["closed"] = True

    class FakeGraphDatabase:
        @staticmethod
        def driver(uri, auth=None):
            calls["uri"] = uri
            calls["auth"] = auth
            return FakeDriver()

    monkeypatch.setitem(sys.modules, "neo4j", types.SimpleNamespace(GraphDatabase=FakeGraphDatabase))
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    from seocho.connectors.neo4j import fetch_schema_records

    records = fetch_schema_records(uri="bolt://mock", database="neo4j")

    assert calls["uri"] == "bolt://mock"
    assert calls["auth"] == ("neo4j", "password")
    assert calls["database"] == "neo4j"
    assert calls["closed"] is True
    assert records[0].source_kind == "neo4j_schema"
    assert ":`WORKS_AT`" in records[0].content


def test_sanitize_metadata_redacts_secret_like_keys() -> None:
    marker = object()
    assert sanitize_metadata({
        "api_token": "secret",
        "headers": {"Authorization": "Bearer secret"},
        "normal": marker,
    }) == {
        "api_token": "[redacted]",
        "headers": {"Authorization": "[redacted]"},
        "normal": str(marker),
    }


def test_connectors_cli_alias_parses() -> None:
    args = build_parser().parse_args([
        "connectors",
        "postgres",
        "--dsn-env",
        "TEST_DATABASE_URL",
        "--schema",
        "public",
        "--dry-run",
    ])

    assert args.command == "connectors"
    assert args.connect_command == "postgres"
    assert args.dsn_env == "TEST_DATABASE_URL"
    assert args.schemas == ["public"]
    assert args.dry_run is True


def test_connectors_cli_config_commands_parse() -> None:
    init_args = build_parser().parse_args(["connect", "init", "seocho.connectors.yaml", "--force"])
    assert init_args.command == "connect"
    assert init_args.connect_command == "init"
    assert init_args.path == "seocho.connectors.yaml"
    assert init_args.force is True

    run_args = build_parser().parse_args([
        "connect",
        "run",
        "seocho.connectors.yaml",
        "--output-dir",
        ".seocho/connectors",
        "--dry-run",
        "--json",
    ])
    assert run_args.connect_command == "run"
    assert run_args.output_dir == ".seocho/connectors"
    assert run_args.dry_run is True
    assert run_args.output_json is True


def test_write_sample_connector_config_refuses_overwrite(tmp_path) -> None:
    path = tmp_path / "seocho.connectors.yaml"
    write_sample_config(path)

    try:
        write_sample_config(path)
    except ConnectorConfigError as exc:
        assert "already exists" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected ConnectorConfigError")

    write_sample_config(path, force=True)
    assert "sources:" in path.read_text(encoding="utf-8")


def test_connector_config_run_writes_jsonl_and_state(tmp_path) -> None:
    config_path = tmp_path / "seocho.connectors.yaml"
    output_dir = tmp_path / "connectors"
    state_path = output_dir / "state.json"
    config_path.write_text(
        f"""
version: 1
output_dir: {output_dir}
state_path: {state_path}
sources:
  - name: pg
    provider: postgres
    dsn_env: TEST_DATABASE_URL
    schemas: [public]
    output: pg.jsonl
""".strip(),
        encoding="utf-8",
    )

    plan = load_connector_config(config_path)

    def fake_fetch(source) -> list[ConnectorRecord]:
        return [
            ConnectorRecord(
                id=f"{source.provider}:schema",
                content="PostgreSQL table app.public.orders",
                provider=source.provider,
                source_kind="postgres_table_schema",
                category=source.category,
            )
        ]

    results = run_connector_plan(plan, fetcher=fake_fetch)

    assert results[0].records == 1
    assert (output_dir / "pg.jsonl").exists()
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["sources"]["pg"]["records"] == 1
    assert state["sources"]["pg"]["record_ids"] == ["postgres:schema"]


def test_connector_config_dry_run_does_not_write_files(tmp_path) -> None:
    config_path = tmp_path / "seocho.connectors.yaml"
    output_dir = tmp_path / "connectors"
    config_path.write_text(
        f"""
version: 1
output_dir: {output_dir}
sources:
  - name: graph
    provider: neo4j
    output: graph.jsonl
""".strip(),
        encoding="utf-8",
    )

    plan = load_connector_config(config_path)
    results = run_connector_plan(
        plan,
        dry_run=True,
        fetcher=lambda source: [
            ConnectorRecord(
                id="neo4j:schema",
                content="Neo4j schema",
                provider=source.provider,
                source_kind="neo4j_schema",
                category=source.category,
            )
        ],
    )

    assert results[0].records == 1
    assert not (output_dir / "graph.jsonl").exists()
    assert not (output_dir / "state.json").exists()


def test_connector_run_cli_writes_outputs_with_mock_materializer(tmp_path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "seocho.connectors.yaml"
    output_dir = tmp_path / "connectors"
    state_path = output_dir / "state.json"
    config_path.write_text(
        f"""
version: 1
output_dir: {output_dir}
state_path: {state_path}
sources:
  - name: graph
    provider: neo4j
    output: graph.jsonl
""".strip(),
        encoding="utf-8",
    )

    def fake_materialize(source) -> list[ConnectorRecord]:
        return [
            ConnectorRecord(
                id=f"{source.provider}:schema",
                content="Mock graph schema",
                provider=source.provider,
                source_kind="neo4j_schema",
                category=source.category,
            )
        ]

    monkeypatch.setattr(connector_config, "materialize_source", fake_materialize)

    exit_code = main(["connect", "run", str(config_path)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "wrote 1 connector record(s)" in output
    assert (output_dir / "graph.jsonl").exists()
    assert state_path.exists()
    records = list(read_records_jsonl(output_dir / "graph.jsonl"))
    assert records[0].id == "neo4j:schema"
