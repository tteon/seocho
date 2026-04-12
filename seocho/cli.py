from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

from .client import Seocho
from .exceptions import SeochoError
from .governance import ArtifactDiff, ArtifactValidationResult
from .local import LocalRuntimeStatus, serve_local_runtime, stop_local_runtime
from .semantic import SemanticArtifact, SemanticArtifactSummary
from .models import ArchiveResult, ChatResponse, GraphTarget, Memory, MemoryCreateResult, SearchResult


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seocho", description="SEOCHO memory-first CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Store one memory")
    add_parser.add_argument("content", help="Memory text to store")
    add_parser.add_argument("--metadata", help="JSON metadata object")
    add_parser.add_argument("--prompt-context", help="JSON semantic prompt context override")
    add_parser.add_argument("--approved-artifact-id", help="Approved semantic artifact to apply")
    add_parser.add_argument("--database", help="Target database override")
    add_parser.add_argument("--category", default="memory", help="Document category")
    add_parser.add_argument("--source-type", default="text", help="Source type: text, csv, or pdf")
    _add_client_options(add_parser, include_scope=True, include_json=True)

    get_parser = subparsers.add_parser("get", help="Fetch one memory")
    get_parser.add_argument("memory_id", help="Memory identifier")
    get_parser.add_argument("--database", help="Target database override")
    _add_client_options(get_parser, include_scope=False, include_json=True)

    search_parser = subparsers.add_parser("search", help="Search memories")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=5, help="Max number of results")
    search_parser.add_argument("--graph-id", action="append", dest="graph_ids", default=[], help="Graph routing hint")
    search_parser.add_argument("--database", action="append", dest="databases", default=[], help="Database scope")
    _add_client_options(search_parser, include_scope=True, include_json=True)

    chat_parser = subparsers.add_parser("chat", help="Ask from memories")
    chat_parser.add_argument("message", help="Question to ask")
    chat_parser.add_argument("--limit", type=int, default=5, help="Max number of retrieval results")
    chat_parser.add_argument("--graph-id", action="append", dest="graph_ids", default=[], help="Graph routing hint")
    chat_parser.add_argument("--database", action="append", dest="databases", default=[], help="Database scope")
    _add_client_options(chat_parser, include_scope=True, include_json=True)

    ask_parser = subparsers.add_parser("ask", help="Ask a question (auto-detects local or server mode)")
    ask_parser.add_argument("message", help="Question to ask")
    ask_parser.add_argument("--limit", type=int, default=5, help="Max number of retrieval results")
    ask_parser.add_argument("--graph-id", action="append", dest="graph_ids", default=[], help="Graph routing hint")
    ask_parser.add_argument("--database", action="append", dest="databases", default=[], help="Database scope")
    ask_parser.add_argument("--local", action="store_true", help="Use local engine (no server needed)")
    ask_parser.add_argument("--schema", default="schema.jsonld", help="Ontology file (local mode)")
    ask_parser.add_argument("--neo4j-uri", default="bolt://localhost:7687", help="Neo4j URI (local mode)")
    ask_parser.add_argument("--neo4j-user", default="neo4j", help="Neo4j user (local mode)")
    ask_parser.add_argument("--neo4j-password", default="password", help="Neo4j password (local mode)")
    ask_parser.add_argument(
        "--provider",
        choices=["openai", "deepseek", "kimi", "grok"],
        default="openai",
        help="OpenAI-compatible LLM provider preset (local mode)",
    )
    ask_parser.add_argument("--model", default="gpt-4o", help="OpenAI model (local mode)")
    ask_parser.add_argument("--llm-base-url", default=None, help="Override the provider base URL (local mode)")
    ask_parser.add_argument("--reasoning", action="store_true", help="Enable reasoning mode (local mode)")
    ask_parser.add_argument("--repair-budget", type=int, default=2, help="Max repair attempts (local mode)")
    _add_client_options(ask_parser, include_scope=True, include_json=True)

    delete_parser = subparsers.add_parser("delete", help="Archive one memory")
    delete_parser.add_argument("memory_id", help="Memory identifier")
    delete_parser.add_argument("--database", help="Target database override")
    _add_client_options(delete_parser, include_scope=False, include_json=True)

    graphs_parser = subparsers.add_parser("graphs", help="List graph targets")
    _add_client_options(graphs_parser, include_scope=False, include_json=True)

    doctor_parser = subparsers.add_parser("doctor", help="Check API health and graph availability")
    _add_client_options(doctor_parser, include_scope=False, include_json=True)

    serve_parser = subparsers.add_parser("serve", help="Start the local SEOCHO docker stack")
    serve_parser.add_argument("--project-dir", default=None, help="Repository root containing docker-compose.yml")
    serve_parser.add_argument("--opik", action="store_true", help="Start optional Opik services too")
    serve_parser.add_argument("--build", action="store_true", help="Rebuild images before starting")
    serve_parser.add_argument("--no-wait", action="store_true", help="Return after docker compose starts")
    serve_parser.add_argument("--timeout", type=float, default=90.0, help="Readiness wait timeout in seconds")
    serve_parser.add_argument(
        "--fallback-openai-key",
        default="dummy-key",
        help="Fallback OPENAI_API_KEY for local verification when no key is set",
    )
    serve_parser.add_argument("--dry-run", action="store_true", help="Print the compose command without running it")
    serve_parser.add_argument("--json", dest="output_json", action="store_true", help="Emit JSON output")

    stop_parser = subparsers.add_parser("stop", help="Stop the local SEOCHO docker stack")
    stop_parser.add_argument("--project-dir", default=None, help="Repository root containing docker-compose.yml")
    stop_parser.add_argument("--volumes", action="store_true", help="Also remove compose volumes")
    stop_parser.add_argument("--dry-run", action="store_true", help="Print the compose command without running it")
    stop_parser.add_argument("--json", dest="output_json", action="store_true", help="Emit JSON output")

    artifacts_parser = subparsers.add_parser("artifacts", help="Manage semantic artifacts")
    artifact_subparsers = artifacts_parser.add_subparsers(dest="artifact_command", required=True)

    artifacts_list_parser = artifact_subparsers.add_parser("list", help="List semantic artifacts")
    artifacts_list_parser.add_argument("--status", choices=["draft", "approved", "deprecated"], default=None)
    _add_client_options(artifacts_list_parser, include_scope=False, include_json=True)

    artifacts_get_parser = artifact_subparsers.add_parser("get", help="Read one semantic artifact")
    artifacts_get_parser.add_argument("artifact_id", help="Semantic artifact identifier")
    _add_client_options(artifacts_get_parser, include_scope=False, include_json=True)

    artifacts_create_parser = artifact_subparsers.add_parser("create-draft", help="Create a draft semantic artifact")
    artifacts_create_parser.add_argument("--artifact-file", required=True, help="Path to artifact JSON payload")
    artifacts_create_parser.add_argument("--name", default=None, help="Override artifact name")
    _add_client_options(artifacts_create_parser, include_scope=False, include_json=True)

    artifacts_approve_parser = artifact_subparsers.add_parser("approve", help="Approve a draft semantic artifact")
    artifacts_approve_parser.add_argument("artifact_id", help="Semantic artifact identifier")
    artifacts_approve_parser.add_argument("--approved-by", required=True, help="Reviewer identifier")
    artifacts_approve_parser.add_argument("--approval-note", default=None, help="Approval note")
    _add_client_options(artifacts_approve_parser, include_scope=False, include_json=True)

    artifacts_deprecate_parser = artifact_subparsers.add_parser("deprecate", help="Deprecate an approved semantic artifact")
    artifacts_deprecate_parser.add_argument("artifact_id", help="Semantic artifact identifier")
    artifacts_deprecate_parser.add_argument("--deprecated-by", required=True, help="Reviewer identifier")
    artifacts_deprecate_parser.add_argument("--deprecation-note", default=None, help="Deprecation note")
    _add_client_options(artifacts_deprecate_parser, include_scope=False, include_json=True)

    artifacts_validate_parser = artifact_subparsers.add_parser("validate", help="Validate one artifact payload")
    validate_source_group = artifacts_validate_parser.add_mutually_exclusive_group(required=True)
    validate_source_group.add_argument("--artifact-id", dest="artifact_id", help="Semantic artifact identifier")
    validate_source_group.add_argument("--artifact-file", dest="artifact_file", help="Artifact JSON payload path")
    _add_client_options(artifacts_validate_parser, include_scope=False, include_json=True)

    artifacts_diff_parser = artifact_subparsers.add_parser("diff", help="Diff two artifact payloads")
    left_group = artifacts_diff_parser.add_mutually_exclusive_group(required=True)
    left_group.add_argument("--left-artifact-id", dest="left_artifact_id", help="Left artifact identifier")
    left_group.add_argument("--left-artifact-file", dest="left_artifact_file", help="Left artifact JSON payload path")
    right_group = artifacts_diff_parser.add_mutually_exclusive_group(required=True)
    right_group.add_argument("--right-artifact-id", dest="right_artifact_id", help="Right artifact identifier")
    right_group.add_argument(
        "--right-artifact-file",
        dest="right_artifact_file",
        help="Right artifact JSON payload path",
    )
    _add_client_options(artifacts_diff_parser, include_scope=False, include_json=True)

    artifacts_apply_parser = artifact_subparsers.add_parser(
        "apply",
        help="Apply one approved artifact to a new memory ingest",
    )
    artifacts_apply_parser.add_argument("artifact_id", help="Approved semantic artifact identifier")
    artifacts_apply_parser.add_argument("content", help="Memory text to store")
    artifacts_apply_parser.add_argument("--metadata", help="JSON metadata object")
    artifacts_apply_parser.add_argument("--prompt-context", help="JSON semantic prompt context override")
    artifacts_apply_parser.add_argument("--database", help="Target database override")
    artifacts_apply_parser.add_argument("--category", default="memory", help="Document category")
    artifacts_apply_parser.add_argument("--source-type", default="text", help="Source type: text, csv, or pdf")
    _add_client_options(artifacts_apply_parser, include_scope=True, include_json=True)

    # --- Local-mode commands (no server needed) ---

    init_parser = subparsers.add_parser("init", help="Create a new ontology interactively")
    init_parser.add_argument("--output", default="schema.jsonld", help="Output file (default: schema.jsonld)")
    init_parser.add_argument("--format", choices=["jsonld", "yaml"], default="jsonld", help="Output format")

    index_parser = subparsers.add_parser("index", help="Index files from a directory into the graph")
    index_parser.add_argument("path", help="File or directory to index")
    index_parser.add_argument("--database", default="neo4j", help="Target database")
    index_parser.add_argument("--schema", default="schema.jsonld", help="Ontology file (JSON-LD or YAML)")
    index_parser.add_argument("--neo4j-uri", default="bolt://localhost:7687", help="Neo4j/DozerDB URI")
    index_parser.add_argument("--neo4j-user", default="neo4j", help="Neo4j user")
    index_parser.add_argument("--neo4j-password", default="password", help="Neo4j password")
    index_parser.add_argument(
        "--provider",
        choices=["openai", "deepseek", "kimi", "grok"],
        default="openai",
        help="OpenAI-compatible LLM provider preset",
    )
    index_parser.add_argument("--model", default="gpt-4o", help="OpenAI model for extraction")
    index_parser.add_argument("--llm-base-url", default=None, help="Override the provider base URL")
    index_parser.add_argument("--force", action="store_true", help="Re-index even if unchanged")
    index_parser.add_argument("--recursive", action="store_true", default=True, help="Scan subdirectories")
    index_parser.add_argument("--strict", action="store_true", help="Reject data that fails SHACL validation")
    index_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    local_ask_parser = subparsers.add_parser("local-ask", help="Ask a question against local graph (no server)")
    local_ask_parser.add_argument("question", help="Question to ask")
    local_ask_parser.add_argument("--database", default="neo4j", help="Target database")
    local_ask_parser.add_argument("--schema", default="schema.jsonld", help="Ontology file")
    local_ask_parser.add_argument("--neo4j-uri", default="bolt://localhost:7687", help="Neo4j URI")
    local_ask_parser.add_argument("--neo4j-user", default="neo4j", help="Neo4j user")
    local_ask_parser.add_argument("--neo4j-password", default="password", help="Neo4j password")
    local_ask_parser.add_argument(
        "--provider",
        choices=["openai", "deepseek", "kimi", "grok"],
        default="openai",
        help="OpenAI-compatible LLM provider preset",
    )
    local_ask_parser.add_argument("--model", default="gpt-4o", help="OpenAI model")
    local_ask_parser.add_argument("--llm-base-url", default=None, help="Override the provider base URL")
    local_ask_parser.add_argument("--reasoning", action="store_true", help="Enable reasoning mode (auto-retry)")
    local_ask_parser.add_argument("--repair-budget", type=int, default=2, help="Max repair attempts")

    status_parser = subparsers.add_parser("status", help="Show graph database status")
    status_parser.add_argument("--database", default="neo4j", help="Target database")
    status_parser.add_argument("--schema", default="schema.jsonld", help="Ontology file")
    status_parser.add_argument("--neo4j-uri", default="bolt://localhost:7687", help="Neo4j URI")
    status_parser.add_argument("--neo4j-user", default="neo4j", help="Neo4j user")
    status_parser.add_argument("--neo4j-password", default="password", help="Neo4j password")
    status_parser.add_argument(
        "--provider",
        choices=["openai", "deepseek", "kimi", "grok"],
        default="openai",
        help="OpenAI-compatible LLM provider preset",
    )
    status_parser.add_argument("--model", default="gpt-4o", help="LLM model used for local queries")
    status_parser.add_argument("--llm-base-url", default=None, help="Override the provider base URL")
    status_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    compare_parser = subparsers.add_parser("compare", help="Compare two configs/models side by side")
    compare_parser.add_argument("input_text", help="Text to extract from (or file path with @)")
    compare_parser.add_argument("--config-a", required=True, help="First ontology file (JSON-LD or YAML)")
    compare_parser.add_argument("--config-b", required=True, help="Second ontology file")
    compare_parser.add_argument("--model-a", default="gpt-4o", help="LLM model for config A")
    compare_parser.add_argument("--model-b", default=None, help="LLM model for config B (default: same as A)")
    compare_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    experiment_parser = subparsers.add_parser("experiment", help="Run multi-axis parameter exploration")
    experiment_parser.add_argument("--input", required=True, help="Input text, @file, or directory path")
    experiment_parser.add_argument("--ontology", action="append", default=[], help="Ontology files to vary (repeat for multiple)")
    experiment_parser.add_argument("--model", action="append", default=[], help="LLM models to vary")
    experiment_parser.add_argument("--chunk-size", type=int, action="append", default=[], dest="chunk_sizes", help="Chunk sizes to vary")
    experiment_parser.add_argument("--temperature", type=float, action="append", default=[], dest="temperatures", help="Temperatures to vary")
    experiment_parser.add_argument("--output", default=None, help="Save results to this directory")
    experiment_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    bundle_parser = subparsers.add_parser("bundle", help="Export or inspect portable runtime bundles")
    bundle_subparsers = bundle_parser.add_subparsers(dest="bundle_command", required=True)

    bundle_export_parser = bundle_subparsers.add_parser("export", help="Export a local SDK configuration as a portable bundle")
    bundle_export_parser.add_argument("--output", required=True, help="Output bundle JSON file")
    bundle_export_parser.add_argument("--app-name", default=None, help="Portable app name")
    bundle_export_parser.add_argument("--database", default="neo4j", help="Default database for the portable runtime")
    bundle_export_parser.add_argument("--schema", default="schema.jsonld", help="Ontology file (JSON-LD or YAML)")
    bundle_export_parser.add_argument("--neo4j-uri", default="bolt://localhost:7687", help="Neo4j/DozerDB URI")
    bundle_export_parser.add_argument("--neo4j-user", default="neo4j", help="Neo4j user")
    bundle_export_parser.add_argument("--neo4j-password", default="password", help="Neo4j password")
    bundle_export_parser.add_argument(
        "--provider",
        choices=["openai", "deepseek", "kimi", "grok"],
        default="openai",
        help="OpenAI-compatible LLM provider preset",
    )
    bundle_export_parser.add_argument("--model", default="gpt-4o", help="OpenAI model")
    bundle_export_parser.add_argument("--llm-base-url", default=None, help="Override the provider base URL")
    bundle_export_parser.add_argument(
        "--prompt-preset",
        default=None,
        choices=["general", "finance", "legal", "medical", "research", "rdf_general", "rdf_fibo"],
        help="Optional extraction prompt preset to serialize into the portable bundle",
    )
    bundle_export_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    bundle_show_parser = bundle_subparsers.add_parser("show", help="Show one portable runtime bundle")
    bundle_show_parser.add_argument("bundle", help="Path to bundle JSON file")
    bundle_show_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_parser = subparsers.add_parser("ontology", help="Offline ontology governance helpers")
    ontology_subparsers = ontology_parser.add_subparsers(dest="ontology_command", required=True)

    ontology_check_parser = ontology_subparsers.add_parser("check", help="Validate one ontology definition")
    ontology_check_parser.add_argument("--schema", required=True, help="Ontology file (JSON-LD or YAML)")
    ontology_check_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_export_parser = ontology_subparsers.add_parser("export", help="Export ontology-derived artifacts")
    ontology_export_parser.add_argument("--schema", required=True, help="Ontology file (JSON-LD or YAML)")
    ontology_export_parser.add_argument(
        "--format",
        required=True,
        choices=["jsonld", "yaml", "dict", "shacl"],
        help="Output artifact format",
    )
    ontology_export_parser.add_argument("--output", default=None, help="Optional output file path")
    ontology_export_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_diff_parser = ontology_subparsers.add_parser("diff", help="Diff two ontology definitions")
    ontology_diff_parser.add_argument("--left", required=True, help="Left ontology file")
    ontology_diff_parser.add_argument("--right", required=True, help="Right ontology file")
    ontology_diff_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    ontology_inspect_parser = ontology_subparsers.add_parser(
        "inspect-owl",
        help="Inspect an OWL ontology with Owlready2 (optional offline dependency)",
    )
    ontology_inspect_parser.add_argument("--source", required=True, help="OWL file path or URI")
    ontology_inspect_parser.add_argument("--json", dest="output_json", action="store_true", help="JSON output")

    serve_http_parser = subparsers.add_parser("serve-http", help="Serve a portable bundle behind a small FastAPI runtime")
    serve_http_parser.add_argument("--bundle", required=True, help="Path to portable bundle JSON file")
    serve_http_parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    serve_http_parser.add_argument("--port", type=int, default=8010, help="Bind port")
    serve_http_parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload mode")

    return parser


LOCAL_COMMANDS = {"init", "index", "local-ask", "status", "compare", "experiment", "bundle", "ontology", "serve-http"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    # Local-mode commands don't need HTTP client
    if args.command in LOCAL_COMMANDS:
        try:
            return _dispatch_local(args)
        except (SeochoError, Exception) as exc:
            print(str(exc), file=sys.stderr)
            return 1

    client: Optional[Seocho] = None
    if args.command not in {"serve", "stop"}:
        client = Seocho(
            base_url=getattr(args, "base_url", None),
            workspace_id=getattr(args, "workspace_id", None),
            user_id=getattr(args, "user_id", None),
            agent_id=getattr(args, "agent_id", None),
            session_id=getattr(args, "session_id", None),
            timeout=getattr(args, "timeout", None),
        )

    try:
        return _dispatch(client, args)
    except SeochoError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()


def _dispatch(client: Optional[Seocho], args: argparse.Namespace) -> int:
    if args.command not in {"serve", "stop"} and client is None:
        raise SeochoError(f"{args.command} requires an initialized SEOCHO client")

    if args.command == "add":
        metadata = _parse_json_object(args.metadata, default={"source": "seocho_cli"}, field_name="--metadata")
        prompt_context = _parse_json_object(args.prompt_context, default=None, field_name="--prompt-context")
        created = client.add_with_details(
            args.content,
            metadata=metadata,
            prompt_context=prompt_context,
            user_id=getattr(args, "user_id", None),
            agent_id=getattr(args, "agent_id", None),
            session_id=getattr(args, "session_id", None),
            approved_artifact_id=args.approved_artifact_id,
            database=args.database,
            category=args.category,
            source_type=args.source_type,
        )
        _print_result(created, args.output_json)
        return 0

    if args.command == "get":
        memory = client.get(args.memory_id, database=args.database)
        _print_result(memory, args.output_json)
        return 0

    if args.command == "search":
        results = client.search(
            args.query,
            limit=args.limit,
            user_id=getattr(args, "user_id", None),
            agent_id=getattr(args, "agent_id", None),
            session_id=getattr(args, "session_id", None),
            graph_ids=args.graph_ids or None,
            databases=args.databases or None,
        )
        _print_search_results(results, args.output_json)
        return 0

    if args.command in {"chat", "ask"}:
        # Auto-detect local mode
        if args.command == "ask" and getattr(args, "local", False):
            local_client = _build_local_client(args)
            try:
                answer = local_client.ask(
                    args.message,
                    database=args.databases[0] if args.databases else "neo4j",
                    reasoning_mode=getattr(args, "reasoning", False),
                    repair_budget=getattr(args, "repair_budget", 2),
                )
                print(answer)
            finally:
                local_client.close()
            return 0

        response = client.chat(
            args.message,
            limit=args.limit,
            user_id=getattr(args, "user_id", None),
            agent_id=getattr(args, "agent_id", None),
            session_id=getattr(args, "session_id", None),
            graph_ids=args.graph_ids or None,
            databases=args.databases or None,
        )
        _print_result(response, args.output_json)
        return 0

    if args.command == "delete":
        result = client.delete(args.memory_id, database=args.database)
        _print_result(result, args.output_json)
        return 0

    if args.command == "graphs":
        graphs = client.graphs()
        _print_graphs(graphs, args.output_json)
        return 0

    if args.command == "doctor":
        payload = {
            "runtime": client.health(scope="runtime"),
            "graphs": [graph.to_dict() for graph in client.graphs()],
        }
        if args.output_json:
            print(json.dumps(payload, indent=2))
        else:
            runtime_status = payload["runtime"].get("status", "unknown")
            print(f"runtime: {runtime_status}")
            print(f"graphs: {len(payload['graphs'])}")
        return 0

    if args.command == "serve":
        status = serve_local_runtime(
            project_dir=args.project_dir,
            with_opik=args.opik,
            build=args.build,
            wait=not args.no_wait,
            timeout=args.timeout,
            fallback_openai_key=args.fallback_openai_key,
            dry_run=args.dry_run,
        )
        _print_result(status, args.output_json)
        return 0

    if args.command == "stop":
        status = stop_local_runtime(
            project_dir=args.project_dir,
            volumes=args.volumes,
            dry_run=args.dry_run,
        )
        _print_result(status, args.output_json)
        return 0

    if args.command == "artifacts":
        if client is None:
            raise SeochoError("artifacts commands require an initialized SEOCHO client")
        return _dispatch_artifacts(client, args)

    raise SeochoError(f"Unknown command: {args.command}")


def _dispatch_artifacts(client: Seocho, args: argparse.Namespace) -> int:
    if args.artifact_command == "list":
        artifacts = client.list_artifacts(status=args.status)
        _print_artifacts(artifacts, args.output_json)
        return 0

    if args.artifact_command == "get":
        artifact = client.get_artifact(args.artifact_id)
        _print_result(artifact, args.output_json)
        return 0

    if args.artifact_command == "create-draft":
        payload = _load_json_file(args.artifact_file, field_name="--artifact-file")
        if args.name:
            payload["name"] = args.name
        artifact = client.create_artifact_draft(payload)
        _print_result(artifact, args.output_json)
        return 0

    if args.artifact_command == "approve":
        artifact = client.approve_artifact(
            args.artifact_id,
            approved_by=args.approved_by,
            approval_note=args.approval_note,
        )
        _print_result(artifact, args.output_json)
        return 0

    if args.artifact_command == "deprecate":
        artifact = client.deprecate_artifact(
            args.artifact_id,
            deprecated_by=args.deprecated_by,
            deprecation_note=args.deprecation_note,
        )
        _print_result(artifact, args.output_json)
        return 0

    if args.artifact_command == "validate":
        artifact = _resolve_artifact_argument(
            client,
            artifact_id=args.artifact_id,
            artifact_file=args.artifact_file,
        )
        result = client.validate_artifact(artifact)
        _print_result(result, args.output_json)
        return 0 if result.ok else 1

    if args.artifact_command == "diff":
        left = _resolve_artifact_argument(
            client,
            artifact_id=args.left_artifact_id,
            artifact_file=args.left_artifact_file,
        )
        right = _resolve_artifact_argument(
            client,
            artifact_id=args.right_artifact_id,
            artifact_file=args.right_artifact_file,
        )
        diff = client.diff_artifacts(left, right)
        _print_result(diff, args.output_json)
        return 0

    if args.artifact_command == "apply":
        metadata = _parse_json_object(args.metadata, default={"source": "seocho_cli"}, field_name="--metadata")
        prompt_context = _parse_json_object(args.prompt_context, default=None, field_name="--prompt-context")
        created = client.apply_artifact(
            args.artifact_id,
            args.content,
            metadata=metadata,
            prompt_context=prompt_context,
            database=args.database,
            category=args.category,
            source_type=args.source_type,
            user_id=getattr(args, "user_id", None),
            agent_id=getattr(args, "agent_id", None),
            session_id=getattr(args, "session_id", None),
        )
        _print_result(created, args.output_json)
        return 0

    raise SeochoError(f"Unknown artifacts command: {args.artifact_command}")


def _add_client_options(
    parser: argparse.ArgumentParser,
    *,
    include_scope: bool,
    include_json: bool,
) -> None:
    parser.add_argument("--base-url", default=None, help="SEOCHO API base URL")
    parser.add_argument("--workspace-id", default=None, help="Workspace scope")
    parser.add_argument("--timeout", type=float, default=None, help="HTTP timeout in seconds")
    if include_scope:
        parser.add_argument("--user-id", default=None, help="User scope")
        parser.add_argument("--agent-id", default=None, help="Agent scope")
        parser.add_argument("--session-id", default=None, help="Session scope")
    if include_json:
        parser.add_argument("--json", dest="output_json", action="store_true", help="Emit JSON output")


def _parse_json_object(
    raw: Optional[str],
    *,
    default: Optional[Dict[str, Any]],
    field_name: str,
) -> Optional[Dict[str, Any]]:
    if not raw:
        return default
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SeochoError(f"{field_name} must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise SeochoError(f"{field_name} must be a JSON object")
    return payload


def _load_json_file(path: str, *, field_name: str) -> Dict[str, Any]:
    file_path = Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SeochoError(f"{field_name} could not be read: {exc}") from exc
    return _parse_json_object(raw, default=None, field_name=field_name) or {}


def _resolve_artifact_argument(
    client: Seocho,
    *,
    artifact_id: Optional[str],
    artifact_file: Optional[str],
) -> Dict[str, Any] | SemanticArtifact:
    if artifact_id:
        return client.get_artifact(artifact_id)
    if artifact_file:
        return _load_json_file(artifact_file, field_name="--artifact-file")
    raise SeochoError("artifact input is required")


def _print_result(value: Any, output_json: bool) -> None:
    if output_json:
        print(json.dumps(_serialize(value), indent=2))
        return

    if isinstance(value, MemoryCreateResult):
        memory = value.memory
        print(f"stored {memory.memory_id} in workspace={memory.workspace_id}")
        return

    if isinstance(value, Memory):
        print(value.content)
        return

    if isinstance(value, ChatResponse):
        print(value.assistant_message)
        return

    if isinstance(value, ArchiveResult):
        print(f"archived {value.memory_id} from {value.database}")
        return

    if isinstance(value, SemanticArtifact):
        print(f"{value.artifact_id} [{value.status}] {value.name}")
        return

    if isinstance(value, ArtifactValidationResult):
        label = "valid" if value.ok else "invalid"
        print(f"artifact {label}: {value.summary.get('error_count', 0)} errors, {value.summary.get('warning_count', 0)} warnings")
        for item in value.errors:
            suffix = f" ({item.path})" if item.path else ""
            print(f"error [{item.code}]{suffix}: {item.message}")
        for item in value.warnings:
            suffix = f" ({item.path})" if item.path else ""
            print(f"warning [{item.code}]{suffix}: {item.message}")
        return

    if isinstance(value, ArtifactDiff):
        print(f"diff {value.left_name} -> {value.right_name}")
        for section in ("metadata", "ontology_classes", "ontology_relationships", "shacl_shapes", "vocabulary_terms"):
            section_changes = value.changes.get(section, {})
            for key in ("changed", "added", "removed"):
                entries = section_changes.get(key, [])
                if entries:
                    print(f"{section} {key}: {', '.join(entries)}")
        return

    if isinstance(value, LocalRuntimeStatus):
        if value.status == "dry_run":
            print(" ".join(value.command))
            return
        if value.action == "serve":
            suffix = " using fallback OPENAI_API_KEY" if value.used_fallback_openai_key else ""
            print(f"runtime {value.status} at {value.api_url}{suffix}")
            print(f"ui: {value.ui_url}")
            print(f"graph: {value.graph_url}")
            return
        print(f"runtime {value.status} in {value.project_dir}")
        return

    print(json.dumps(_serialize(value), indent=2))


def _print_search_results(results: Sequence[SearchResult], output_json: bool) -> None:
    if output_json:
        print(json.dumps([item.to_dict() for item in results], indent=2))
        return

    if not results:
        print("no memories found")
        return

    for index, result in enumerate(results, start=1):
        preview = result.content_preview or result.content
        print(f"{index}. [{result.score:.2f}] {preview}")


def _print_graphs(graphs: Iterable[GraphTarget], output_json: bool) -> None:
    graph_list = list(graphs)
    if output_json:
        print(json.dumps([graph.to_dict() for graph in graph_list], indent=2))
        return

    if not graph_list:
        print("no graph targets configured")
        return

    for graph in graph_list:
        description = f" - {graph.description}" if graph.description else ""
        print(f"{graph.graph_id} ({graph.database}){description}")


def _print_artifacts(artifacts: Sequence[SemanticArtifactSummary], output_json: bool) -> None:
    if output_json:
        print(json.dumps([artifact.to_dict() for artifact in artifacts], indent=2))
        return

    if not artifacts:
        print("no semantic artifacts found")
        return

    for artifact in artifacts:
        print(f"{artifact.artifact_id} [{artifact.status}] {artifact.name or artifact.artifact_id}")


def _serialize(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


# ======================================================================
# Local-mode command handlers
# ======================================================================


def _dispatch_local(args: argparse.Namespace) -> int:
    if args.command == "init":
        return _cmd_init(args)
    if args.command == "index":
        return _cmd_index(args)
    if args.command == "local-ask":
        return _cmd_local_ask(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "compare":
        return _cmd_compare(args)
    if args.command == "experiment":
        return _cmd_experiment(args)
    if args.command == "bundle":
        return _cmd_bundle(args)
    if args.command == "ontology":
        return _cmd_ontology(args)
    if args.command == "serve-http":
        return _cmd_serve_http(args)
    raise SeochoError(f"Unknown local command: {args.command}")


def _cmd_init(args: argparse.Namespace) -> int:
    """Interactive ontology creation."""
    from .ontology import NodeDef, Ontology, P, RelDef

    print("SEOCHO Ontology Setup")
    print("=" * 40)

    name = input("Domain name (e.g. news, finance, hr): ").strip() or "my_domain"
    print()

    # Collect node types
    print("Define entity types (empty line to finish):")
    nodes: Dict[str, NodeDef] = {}
    while True:
        label = input("  Entity type (e.g. Person, Company): ").strip()
        if not label:
            break
        label = label[0].upper() + label[1:] if label else label
        desc = input(f"  Description for {label}: ").strip()
        props_input = input(f"  Properties for {label} (comma-separated, e.g. name,age,role): ").strip()
        props: Dict[str, P] = {}
        if props_input:
            for i, pname in enumerate(props_input.split(",")):
                pname = pname.strip()
                if not pname:
                    continue
                # First property is unique by default
                props[pname] = P(str, unique=(i == 0))
        else:
            props["name"] = P(str, unique=True)
        nodes[label] = NodeDef(description=desc, properties=props)
        print(f"  Added: {label} ({len(props)} properties)")
        print()

    if not nodes:
        print("At least one entity type is required.")
        return 1

    # Collect relationships
    print()
    print("Define relationships (empty line to finish):")
    relationships: Dict[str, RelDef] = {}
    node_labels = list(nodes.keys())
    while True:
        rtype = input("  Relationship type (e.g. WORKS_AT, FOUNDED): ").strip().upper().replace(" ", "_")
        if not rtype:
            break
        print(f"  Available entities: {', '.join(node_labels)}")
        source = input(f"  Source entity for {rtype}: ").strip()
        target = input(f"  Target entity for {rtype}: ").strip()
        if source not in nodes or target not in nodes:
            print(f"  Warning: {source} or {target} not in defined entities, adding anyway")
        relationships[rtype] = RelDef(source=source, target=target)
        print(f"  Added: ({source})-[:{rtype}]->({target})")
        print()

    ontology = Ontology(name=name, nodes=nodes, relationships=relationships)

    # Save ontology
    output = args.output
    if args.format == "yaml" or output.endswith(".yaml") or output.endswith(".yml"):
        ontology.to_yaml(output)
    else:
        ontology.to_jsonld(output)

    # Save project config (.seocho.toml)
    from .config_file import write_config
    config_path = Path(".seocho.toml")
    if not config_path.exists():
        write_config(config_path, schema=output, database=name)
        print(f"Project config saved to .seocho.toml")

    print(f"Ontology saved to {output}")
    print(f"  {len(nodes)} entity types, {len(relationships)} relationships")
    print()
    print("Next steps:")
    print(f"  seocho index ./your_data/")
    print(f"  seocho ask --local 'your question here'")
    return 0


def _load_local_ontology(schema_path: str) -> Any:
    """Load ontology from file."""
    from .ontology import Ontology

    path = Path(schema_path)
    if not path.exists():
        raise SeochoError(f"Schema file not found: {schema_path}\nRun 'seocho init' to create one.")
    if path.suffix in (".yaml", ".yml"):
        return Ontology.from_yaml(path)
    return Ontology.from_jsonld(path)


def _build_local_client(args: argparse.Namespace) -> Seocho:
    """Build a local-mode Seocho client from CLI args + .seocho.toml defaults."""
    from .config_file import get_default, load_config
    from .query.strategy import PRESET_PROMPTS
    from .store.graph import Neo4jGraphStore
    from .store.llm import create_llm_backend

    cfg = load_config()

    schema = getattr(args, "schema", None) or get_default(cfg, "project", "schema", "schema.jsonld")
    neo4j_uri = getattr(args, "neo4j_uri", None) or get_default(cfg, "neo4j", "uri", "bolt://localhost:7687")
    neo4j_user = getattr(args, "neo4j_user", None) or get_default(cfg, "neo4j", "user", "neo4j")
    neo4j_password = getattr(args, "neo4j_password", None) or get_default(cfg, "neo4j", "password", "password")
    provider = getattr(args, "provider", None) or get_default(cfg, "llm", "provider", "openai")
    model = getattr(args, "model", None) or get_default(cfg, "llm", "model", "gpt-4o")
    llm_base_url = getattr(args, "llm_base_url", None) or get_default(cfg, "llm", "base_url", None)

    ontology = _load_local_ontology(schema)
    store = Neo4jGraphStore(neo4j_uri, neo4j_user, neo4j_password)
    llm = create_llm_backend(provider=provider, model=model, base_url=llm_base_url)
    prompt_preset_name = getattr(args, "prompt_preset", None)
    extraction_prompt = PRESET_PROMPTS[prompt_preset_name] if prompt_preset_name else None
    return Seocho(ontology=ontology, graph_store=store, llm=llm, extraction_prompt=extraction_prompt)


def _cmd_index(args: argparse.Namespace) -> int:
    """Index files or directory."""
    client = _build_local_client(args)
    path = Path(args.path)

    try:
        if path.is_dir():
            result = client.index_directory(
                str(path),
                database=args.database,
                recursive=args.recursive,
                force=args.force,
                on_file=lambda f, i, t: print(f"  [{i+1}/{t}] {Path(f).name}") if not getattr(args, "output_json", False) else None,
            )
            if getattr(args, "output_json", False):
                print(json.dumps(result, indent=2))
            else:
                print()
                print(f"Indexed {result['files_indexed']} files")
                if result["files_unchanged"]:
                    print(f"  {result['files_unchanged']} unchanged (skipped)")
                if result["files_skipped"]:
                    print(f"  {result['files_skipped']} skipped (unsupported or empty)")
                if result["files_failed"]:
                    print(f"  {result['files_failed']} failed")
        elif path.is_file():
            result = client.index_file(
                str(path),
                database=args.database,
                force=args.force,
            )
            if getattr(args, "output_json", False):
                print(json.dumps(result, indent=2))
            else:
                print(f"{result['status']}: {path.name}")
                if result.get("indexing"):
                    idx = result["indexing"]
                    print(f"  nodes: {idx.get('total_nodes', 0)}, relationships: {idx.get('total_relationships', 0)}")
        else:
            print(f"Path not found: {path}", file=sys.stderr)
            return 1
    finally:
        client.close()

    return 0


def _cmd_local_ask(args: argparse.Namespace) -> int:
    """Ask a question against local graph."""
    client = _build_local_client(args)

    try:
        answer = client.ask(
            args.question,
            database=args.database,
            reasoning_mode=args.reasoning,
            repair_budget=args.repair_budget,
        )
        print(answer)
    finally:
        client.close()

    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Show graph database status."""
    from .store.graph import Neo4jGraphStore

    ontology = _load_local_ontology(args.schema)
    store = Neo4jGraphStore(args.neo4j_uri, args.neo4j_user, args.neo4j_password)

    try:
        schema = store.get_schema(database=args.database)
        node_count = store.query(
            "MATCH (n) RETURN count(n) AS cnt",
            database=args.database,
        )
        rel_count = store.query(
            "MATCH ()-[r]->() RETURN count(r) AS cnt",
            database=args.database,
        )

        status = {
            "database": args.database,
            "labels": schema.get("labels", []),
            "relationship_types": schema.get("relationship_types", []),
            "total_nodes": node_count[0]["cnt"] if node_count else 0,
            "total_relationships": rel_count[0]["cnt"] if rel_count else 0,
            "ontology": ontology.name,
            "ontology_nodes": len(ontology.nodes),
            "ontology_relationships": len(ontology.relationships),
        }

        if getattr(args, "output_json", False):
            print(json.dumps(status, indent=2))
        else:
            print(f"Database: {status['database']}")
            print(f"  Nodes: {status['total_nodes']} ({', '.join(status['labels']) or 'none'})")
            print(f"  Relationships: {status['total_relationships']} ({', '.join(status['relationship_types']) or 'none'})")
            print(f"  Ontology: {status['ontology']} ({status['ontology_nodes']} types, {status['ontology_relationships']} rels)")
    except Exception as exc:
        print(f"Could not connect to database: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    """Compare two ontology/model configs on the same input."""
    from .experiment import ExperimentRunner
    from .store.llm import OpenAIBackend

    # Read input
    input_text = args.input_text
    if input_text.startswith("@"):
        fpath = Path(input_text[1:])
        if not fpath.exists():
            print(f"File not found: {fpath}", file=sys.stderr)
            return 1
        input_text = fpath.read_text(encoding="utf-8")

    onto_a = _load_local_ontology(args.config_a)
    onto_b = _load_local_ontology(args.config_b)

    model_a = args.model_a
    model_b = args.model_b or model_a

    llm_a = OpenAIBackend(model=model_a)
    llm_b = OpenAIBackend(model=model_b) if model_b != model_a else llm_a

    runner = ExperimentRunner()

    print(f"Running config A ({onto_a.name}, {model_a})...")
    result_a = runner.run(ontology=onto_a, llm=llm_a, text=input_text, config_name="A")

    print(f"Running config B ({onto_b.name}, {model_b})...")
    result_b = runner.run(ontology=onto_b, llm=llm_b, text=input_text, config_name="B")

    comparison = runner.compare(result_a, result_b)

    if getattr(args, "output_json", False):
        print(json.dumps(comparison.to_dict(), indent=2))
    else:
        print()
        print(comparison.summary())

    return 0


def _cmd_experiment(args: argparse.Namespace) -> int:
    """Run multi-axis experiment exploration."""
    from .experiment import Workbench

    # Resolve input
    input_arg = args.input
    input_texts: List[str] = []
    input_dir: Optional[str] = None

    if Path(input_arg).is_dir():
        input_dir = input_arg
    elif input_arg.startswith("@"):
        fpath = Path(input_arg[1:])
        if fpath.exists():
            input_texts = [fpath.read_text(encoding="utf-8")]
        else:
            print(f"File not found: {fpath}", file=sys.stderr)
            return 1
    else:
        input_texts = [input_arg]

    wb = Workbench(input_texts=input_texts, input_dir=input_dir)

    # Register axes
    if args.ontology:
        wb.vary("ontology", args.ontology)
    if args.model:
        wb.vary("model", args.model)
    if args.chunk_sizes:
        wb.vary("chunk_size", args.chunk_sizes)
    if args.temperatures:
        wb.vary("temperature", args.temperatures)

    if wb.total_combinations == 0:
        print("No axes defined. Use --ontology, --model, --chunk-size, --temperature", file=sys.stderr)
        return 1

    print(f"Running {wb.total_combinations} experiment combinations...")
    wb.on_run(lambda i, t, p: print(f"  [{i}/{t}] {' | '.join(f'{k}={v}' for k, v in p.items())}"))

    results = wb.run_all()

    if getattr(args, "output_json", False):
        print(json.dumps(results.to_dicts(), indent=2))
    else:
        print()
        print(results.leaderboard())

    if args.output:
        saved = results.save(args.output)
        print(f"\nResults saved to {saved}/")

    return 0


def _cmd_bundle(args: argparse.Namespace) -> int:
    if args.bundle_command == "export":
        return _cmd_bundle_export(args)
    if args.bundle_command == "show":
        return _cmd_bundle_show(args)
    raise SeochoError(f"Unknown bundle command: {args.bundle_command}")


def _cmd_bundle_export(args: argparse.Namespace) -> int:
    client = _build_local_client(args)
    try:
        bundle = client.export_runtime_bundle(
            args.output,
            app_name=args.app_name,
            default_database=args.database,
        )
        payload = bundle.to_dict()
        if getattr(args, "output_json", False):
            print(json.dumps(payload, indent=2))
        else:
            print(f"Bundle exported to {args.output}")
            print(f"  app_name: {payload['app_name']}")
            print(f"  workspace_id: {payload['workspace_id']}")
            print(f"  default_database: {payload['graph_store']['default_database']}")
            print(f"  route graph count: {len(payload.get('graphs', []))}")
        return 0
    finally:
        client.close()


def _cmd_bundle_show(args: argparse.Namespace) -> int:
    from .runtime_bundle import RuntimeBundle

    bundle = RuntimeBundle.load(args.bundle)
    payload = bundle.to_dict()
    if getattr(args, "output_json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(f"Bundle: {args.bundle}")
        print(f"  app_name: {bundle.app_name}")
        print(f"  workspace_id: {bundle.workspace_id}")
        print(f"  default_database: {bundle.default_database}")
        print(f"  graph_store: {bundle.graph_store.kind} @ {bundle.graph_store.uri}")
        print(f"  llm: {bundle.llm.kind} / {bundle.llm.model}")
        print(f"  graphs: {', '.join(item.graph_id for item in bundle.graphs) or 'none'}")
    return 0


def _cmd_ontology(args: argparse.Namespace) -> int:
    from .ontology_governance import (
        check_ontology,
        diff_ontologies,
        export_ontology_payload,
        inspect_owl_ontology,
        load_ontology_file,
    )
    import yaml

    if args.ontology_command == "check":
        ontology = load_ontology_file(args.schema)
        result = check_ontology(ontology)
        if getattr(args, "output_json", False):
            print(json.dumps(result.to_dict(), indent=2))
        else:
            status = "ok" if result.ok else "invalid"
            print(f"ontology {status}: {result.ontology_name}@{result.ontology_version}")
            print(
                f"  graph_model={result.stats['graph_model']} "
                f"nodes={result.stats['node_count']} relationships={result.stats['relationship_count']}"
            )
            for item in result.errors:
                print(f"error: {item}")
            for item in result.warnings:
                print(f"warning: {item}")
        return 0 if result.ok else 1

    if args.ontology_command == "export":
        ontology = load_ontology_file(args.schema)
        payload = export_ontology_payload(ontology, output_format=args.format)

        if args.format == "yaml":
            rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        else:
            rendered = json.dumps(payload, indent=2, ensure_ascii=False)

        if args.output:
            Path(args.output).write_text(rendered + ("" if rendered.endswith("\n") else "\n"), encoding="utf-8")

        if getattr(args, "output_json", False):
            print(json.dumps({"format": args.format, "output": args.output, "payload": payload}, indent=2, ensure_ascii=False))
        elif args.output:
            print(f"exported {args.format} to {args.output}")
        else:
            print(rendered)
        return 0

    if args.ontology_command == "diff":
        left = load_ontology_file(args.left)
        right = load_ontology_file(args.right)
        diff = diff_ontologies(left, right)
        if getattr(args, "output_json", False):
            print(json.dumps(diff.to_dict(), indent=2))
        else:
            print(f"diff {diff.left_name} -> {diff.right_name}")
            for section_name, section_changes in diff.changes.items():
                for change_kind, values in section_changes.items():
                    if values:
                        print(f"{section_name} {change_kind}: {', '.join(values)}")
        return 0

    if args.ontology_command == "inspect-owl":
        inspection = inspect_owl_ontology(args.source)
        if getattr(args, "output_json", False):
            print(json.dumps(inspection.to_dict(), indent=2))
        else:
            if not inspection.available:
                print(inspection.error or "owlready2 unavailable")
                return 1
            if inspection.error:
                print(f"owlready2 inspection failed: {inspection.error}")
                return 1
            print(f"owlready2 source: {inspection.source}")
            print(
                "  "
                f"classes={inspection.stats.get('class_count', 0)} "
                f"properties={inspection.stats.get('property_count', 0)} "
                f"individuals={inspection.stats.get('individual_count', 0)} "
                f"imports={inspection.stats.get('import_count', 0)}"
            )
        return 0 if inspection.available and inspection.error is None else 1

    raise SeochoError(f"Unknown ontology command: {args.ontology_command}")


def _cmd_serve_http(args: argparse.Namespace) -> int:
    from .http_runtime import create_bundle_runtime_app
    from .runtime_bundle import RuntimeBundle

    try:
        import uvicorn
    except ImportError as exc:
        raise SeochoError(
            "serve-http requires uvicorn. Install the repository dev dependencies or add uvicorn."
        ) from exc

    bundle = RuntimeBundle.load(args.bundle)
    app = create_bundle_runtime_app(bundle)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
    return 0
