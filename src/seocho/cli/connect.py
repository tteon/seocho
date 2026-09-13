"""Connect command: parser and local execution owner."""

from __future__ import annotations
import argparse
import json
from typing import Any, Sequence
from ..exceptions import SeochoError


def register(subparsers: Any) -> None:
    connect_parser = subparsers.add_parser(
        "connect",
        aliases=["connectors"],
        help="Materialize external sources as SEOCHO JSONL records",
    )
    connect_subparsers = connect_parser.add_subparsers(
        dest="connect_command", required=True
    )

    connect_init_parser = connect_subparsers.add_parser(
        "init", help="Write a starter seocho.connectors.yaml"
    )
    connect_init_parser.add_argument(
        "path",
        nargs="?",
        default="seocho.connectors.yaml",
        help="Config path to create",
    )
    connect_init_parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing config"
    )

    connect_run_parser = connect_subparsers.add_parser(
        "run", help="Materialize all sources in a connector config"
    )
    connect_run_parser.add_argument(
        "config",
        nargs="?",
        default="seocho.connectors.yaml",
        help="Connector config path",
    )
    connect_run_parser.add_argument(
        "--output-dir", default=None, help="Override config output_dir"
    )
    connect_run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and summarize without writing JSONL",
    )
    connect_run_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="Emit JSON"
    )

    notion_parser = connect_subparsers.add_parser(
        "notion", help="Export Notion pages or data-source rows"
    )
    notion_parser.add_argument(
        "--data-source-id", action="append", default=[], help="Notion data source id"
    )
    notion_parser.add_argument(
        "--page-id", action="append", default=[], help="Notion page id"
    )
    notion_parser.add_argument(
        "--token-env",
        default="NOTION_TOKEN",
        help="Env var containing the Notion token",
    )
    notion_parser.add_argument(
        "--notion-version", default="2026-03-11", help="Notion-Version header"
    )
    notion_parser.add_argument(
        "--category", default="notion", help="SEOCHO document category"
    )
    notion_parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Max Notion API pages per data source",
    )
    notion_parser.add_argument(
        "--no-blocks", action="store_true", help="Export page properties only"
    )
    notion_parser.add_argument(
        "--output", default=".seocho/connectors/notion.jsonl", help="Output JSONL path"
    )
    notion_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and summarize without writing JSONL",
    )
    notion_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="Emit JSON"
    )

    slack_parser = connect_subparsers.add_parser(
        "slack", help="Export Slack channel messages"
    )
    slack_parser.add_argument(
        "--channel",
        action="append",
        dest="channels",
        default=[],
        help="Slack channel id",
    )
    slack_parser.add_argument(
        "--token-env",
        default="SLACK_BOT_TOKEN",
        help="Env var containing the Slack token",
    )
    slack_parser.add_argument(
        "--team-id", default="", help="Slack team/workspace id for stable provenance"
    )
    slack_parser.add_argument(
        "--channel-name", default="", help="Optional channel display name"
    )
    slack_parser.add_argument(
        "--category", default="slack", help="SEOCHO document category"
    )
    slack_parser.add_argument(
        "--limit",
        type=int,
        default=15,
        help="Messages per Slack page; 15 is safe for new non-Marketplace apps, use 200 for Tier 3 apps",
    )
    slack_parser.add_argument(
        "--max-pages", type=int, default=None, help="Max Slack pages per channel"
    )
    slack_parser.add_argument(
        "--threads",
        action="store_true",
        help="Group replied messages as thread records",
    )
    slack_parser.add_argument(
        "--output", default=".seocho/connectors/slack.jsonl", help="Output JSONL path"
    )
    slack_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and summarize without writing JSONL",
    )
    slack_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="Emit JSON"
    )

    datahub_parser = connect_subparsers.add_parser(
        "datahub", help="Export DataHub dataset metadata"
    )
    datahub_parser.add_argument(
        "--server", required=True, help="DataHub frontend/GMS URL"
    )
    datahub_parser.add_argument(
        "--token-env",
        default="DATAHUB_TOKEN",
        help="Env var containing a DataHub token",
    )
    datahub_parser.add_argument("--query", default="*", help="DataHub search query")
    datahub_parser.add_argument(
        "--limit", type=int, default=100, help="Max datasets to export"
    )
    datahub_parser.add_argument(
        "--category", default="datahub", help="SEOCHO document category"
    )
    datahub_parser.add_argument(
        "--output", default=".seocho/connectors/datahub.jsonl", help="Output JSONL path"
    )
    datahub_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and summarize without writing JSONL",
    )
    datahub_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="Emit JSON"
    )

    postgres_parser = connect_subparsers.add_parser(
        "postgres", help="Export PostgreSQL schema metadata"
    )
    postgres_parser.add_argument(
        "--dsn-env",
        default="DATABASE_URL",
        help="Env var containing the PostgreSQL DSN",
    )
    postgres_parser.add_argument(
        "--schema",
        action="append",
        dest="schemas",
        default=[],
        help="Schema to include",
    )
    postgres_parser.add_argument(
        "--database-name", default="", help="Logical database name for provenance"
    )
    postgres_parser.add_argument(
        "--category", default="postgres", help="SEOCHO document category"
    )
    postgres_parser.add_argument(
        "--output",
        default=".seocho/connectors/postgres.jsonl",
        help="Output JSONL path",
    )
    postgres_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and summarize without writing JSONL",
    )
    postgres_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="Emit JSON"
    )

    neo4j_parser = connect_subparsers.add_parser(
        "neo4j", help="Export Neo4j/DozerDB schema metadata"
    )
    neo4j_parser.add_argument(
        "--uri-env", default="NEO4J_URI", help="Env var containing the Bolt URI"
    )
    neo4j_parser.add_argument(
        "--user-env", default="NEO4J_USER", help="Env var containing the graph user"
    )
    neo4j_parser.add_argument(
        "--password-env",
        default="NEO4J_PASSWORD",
        help="Env var containing the graph password",
    )
    neo4j_parser.add_argument(
        "--database", default="", help="Neo4j/DozerDB database name"
    )
    neo4j_parser.add_argument(
        "--category", default="neo4j", help="SEOCHO document category"
    )
    neo4j_parser.add_argument(
        "--output", default=".seocho/connectors/neo4j.jsonl", help="Output JSONL path"
    )
    neo4j_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and summarize without writing JSONL",
    )
    neo4j_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="Emit JSON"
    )


def _print_connect_result(
    *,
    provider: str,
    records: Sequence[Any],
    output: str,
    dry_run: bool,
    output_json: bool,
) -> None:
    from ..connectors import summarize_records

    summary = summarize_records(records)
    payload = {
        "provider": provider,
        "output": output,
        "dry_run": dry_run,
        **summary,
    }
    if output_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    action = "would write" if dry_run else "wrote"
    print(f"{action} {summary['records']} {provider} record(s) to {output}")
    if not dry_run:
        print()
        print("Next:")
        print(f"  set documents.path in seocho.run.yaml to: {output}")
        print("  seocho run --dry-run")
        print("  seocho run")


def _print_connect_plan_result(
    *,
    results: Sequence[Any],
    output_dir: str,
    state_path: str,
    dry_run: bool,
    output_json: bool,
) -> None:
    payload = {
        "dry_run": dry_run,
        "output_dir": output_dir,
        "state_path": state_path,
        "records": sum(int(getattr(result, "records", 0)) for result in results),
        "sources": [result.to_dict() for result in results],
    }
    if output_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    action = "would write" if dry_run else "wrote"
    print(
        f"{action} {payload['records']} connector record(s) from {len(results)} source(s)"
    )
    for result in results:
        print(
            f"  - {result.name}: {result.records} {result.provider} record(s) -> {result.output}"
        )
    if not dry_run:
        print(f"state: {state_path}")
        print()
        print("Next:")
        print(f"  set documents.path in seocho.run.yaml to: {output_dir}")
        print("  seocho run --dry-run")
        print("  seocho run")


def handle(args: argparse.Namespace) -> int:
    """Materialize external ecosystem data as SEOCHO JSONL records."""

    provider = args.connect_command
    if provider == "init":
        from ..connectors.config import write_sample_config

        path = write_sample_config(args.path, force=args.force)
        print(f"Created connector config at {path}")
        print()
        print("Next:")
        print(f"  edit {path}")
        print(f"  seocho connect run {path} --dry-run")
        print(f"  seocho connect run {path}")
        return 0

    if provider == "run":
        from ..connectors.config import load_connector_config, run_connector_plan

        plan = load_connector_config(args.config)
        if args.output_dir:
            plan.output_dir = args.output_dir
        results = run_connector_plan(plan, dry_run=args.dry_run)
        _print_connect_plan_result(
            results=results,
            output_dir=plan.output_dir,
            state_path=plan.state_path,
            dry_run=args.dry_run,
            output_json=args.output_json,
        )
        return 0

    from ..connectors import write_records_jsonl

    records: list[Any]
    if provider == "notion":
        if not args.data_source_id and not args.page_id:
            raise SeochoError("connect notion requires --data-source-id or --page-id.")
        from ..connectors.notion import fetch_data_source_records, fetch_page_records

        records = []
        if args.page_id:
            records.extend(
                fetch_page_records(
                    args.page_id,
                    token_env=args.token_env,
                    notion_version=args.notion_version,
                    category=args.category,
                    include_blocks=not args.no_blocks,
                )
            )
        for data_source_id in args.data_source_id:
            records.extend(
                fetch_data_source_records(
                    data_source_id,
                    token_env=args.token_env,
                    notion_version=args.notion_version,
                    category=args.category,
                    max_pages=args.max_pages,
                    include_blocks=not args.no_blocks,
                )
            )
    elif provider == "slack":
        if not args.channels:
            raise SeochoError("connect slack requires at least one --channel.")
        from ..connectors.slack import fetch_channel_records

        records = fetch_channel_records(
            args.channels,
            token_env=args.token_env,
            team_id=args.team_id,
            channel_name=args.channel_name,
            category=args.category,
            limit=args.limit,
            max_pages=args.max_pages,
            include_threads=args.threads,
        )
    elif provider == "datahub":
        from ..connectors.datahub import fetch_dataset_records

        records = fetch_dataset_records(
            server=args.server,
            token_env=args.token_env,
            query_text=args.query,
            limit=args.limit,
            category=args.category,
        )
    elif provider == "postgres":
        from ..connectors.postgres import fetch_schema_records

        records = fetch_schema_records(
            dsn_env=args.dsn_env,
            schemas=args.schemas or None,
            database=args.database_name,
            category=args.category,
        )
    elif provider == "neo4j":
        from ..connectors.neo4j import fetch_schema_records

        records = fetch_schema_records(
            uri_env=args.uri_env,
            user_env=args.user_env,
            password_env=args.password_env,
            database=args.database,
            category=args.category,
        )
    else:
        raise SeochoError(f"Unknown connector provider: {provider}")

    if not args.dry_run:
        write_records_jsonl(records, args.output)
    _print_connect_result(
        provider=provider,
        records=records,
        output=args.output,
        dry_run=args.dry_run,
        output_json=args.output_json,
    )
    return 0
