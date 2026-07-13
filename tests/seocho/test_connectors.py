from __future__ import annotations

import json
from dataclasses import dataclass

from seocho.connectors import (
    read_records_jsonl,
    records_from_langchain_documents,
    records_from_llamaindex_documents,
    write_records_jsonl,
)
from seocho.connectors.datahub import dataset_entity_to_record
from seocho.connectors.notion import blocks_to_markdown, page_to_record
from seocho.connectors.neo4j import records_from_schema_rows as records_from_neo4j_schema_rows
from seocho.connectors.postgres import records_from_schema_rows
from seocho.connectors.records import sanitize_metadata
from seocho.connectors.slack import message_to_record, thread_to_record
from seocho.cli import build_parser
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
