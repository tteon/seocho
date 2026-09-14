"""Legacy command argument contracts, grouped by execution responsibility."""

from __future__ import annotations
from typing import Any
from .options import LOCAL_PROVIDERS, add_client_options as _add_client_options


def register_memory_commands(subparsers: Any) -> None:
    add_parser = subparsers.add_parser("add", help="Store one memory")
    add_parser.add_argument("content", help="Memory text to store")
    add_parser.add_argument("--metadata", help="JSON metadata object")
    add_parser.add_argument(
        "--prompt-context", help="JSON semantic prompt context override"
    )
    add_parser.add_argument(
        "--approved-artifact-id", help="Approved semantic artifact to apply"
    )
    add_parser.add_argument("--database", help="Target database override")
    add_parser.add_argument("--category", default="memory", help="Document category")
    add_parser.add_argument(
        "--source-type", default="text", help="Source type: text, csv, or pdf"
    )
    _add_client_options(add_parser, include_scope=True, include_json=True)

    get_parser = subparsers.add_parser("get", help="Fetch one memory")
    get_parser.add_argument("memory_id", help="Memory identifier")
    get_parser.add_argument("--database", help="Target database override")
    _add_client_options(get_parser, include_scope=False, include_json=True)

    search_parser = subparsers.add_parser("search", help="Search memories")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument(
        "--limit", type=int, default=5, help="Max number of results"
    )
    search_parser.add_argument(
        "--graph-id",
        action="append",
        dest="graph_ids",
        default=[],
        help="Graph routing hint",
    )
    search_parser.add_argument(
        "--database",
        action="append",
        dest="databases",
        default=[],
        help="Database scope",
    )
    _add_client_options(search_parser, include_scope=True, include_json=True)

    chat_parser = subparsers.add_parser("chat", help="Ask from memories")
    chat_parser.add_argument("message", help="Question to ask")
    chat_parser.add_argument(
        "--limit", type=int, default=5, help="Max number of retrieval results"
    )
    chat_parser.add_argument(
        "--graph-id",
        action="append",
        dest="graph_ids",
        default=[],
        help="Graph routing hint",
    )
    chat_parser.add_argument(
        "--database",
        action="append",
        dest="databases",
        default=[],
        help="Database scope",
    )
    _add_client_options(chat_parser, include_scope=True, include_json=True)

    ask_parser = subparsers.add_parser(
        "ask", help="Ask a question (auto-detects local or server mode)"
    )
    ask_parser.add_argument("message", help="Question to ask")
    ask_parser.add_argument(
        "--limit", type=int, default=5, help="Max number of retrieval results"
    )
    ask_parser.add_argument(
        "--graph-id",
        action="append",
        dest="graph_ids",
        default=[],
        help="Graph routing hint",
    )
    ask_parser.add_argument(
        "--database",
        action="append",
        dest="databases",
        default=[],
        help="Database scope",
    )
    ask_parser.add_argument(
        "--local", action="store_true", help="Use local engine (no server needed)"
    )
    ask_parser.add_argument(
        "--schema",
        default="schema.jsonld",
        help="Ontology file (JSON-LD, YAML, or TTL)",
    )
    ask_parser.add_argument(
        "--neo4j-uri", default="bolt://localhost:7687", help="Neo4j URI (local mode)"
    )
    ask_parser.add_argument(
        "--neo4j-user", default="neo4j", help="Neo4j user (local mode)"
    )
    ask_parser.add_argument(
        "--neo4j-password", default="password", help="Neo4j password (local mode)"
    )
    ask_parser.add_argument(
        "--provider",
        choices=LOCAL_PROVIDERS,
        default="mara",
        help="OpenAI-compatible LLM provider preset (local mode)",
    )
    ask_parser.add_argument("--model", default=None, help="LLM model (local mode)")
    ask_parser.add_argument(
        "--llm-base-url",
        default=None,
        help="Override the provider base URL (local mode)",
    )
    ask_parser.add_argument(
        "--reasoning", action="store_true", help="Enable reasoning mode (local mode)"
    )
    ask_parser.add_argument(
        "--repair-budget", type=int, default=2, help="Max repair attempts (local mode)"
    )
    _add_client_options(ask_parser, include_scope=True, include_json=True)

    delete_parser = subparsers.add_parser("delete", help="Archive one memory")
    delete_parser.add_argument("memory_id", help="Memory identifier")
    delete_parser.add_argument("--database", help="Target database override")
    _add_client_options(delete_parser, include_scope=False, include_json=True)

    graphs_parser = subparsers.add_parser("graphs", help="List graph targets")
    _add_client_options(graphs_parser, include_scope=False, include_json=True)

    doctor_parser = subparsers.add_parser(
        "doctor", help="Check API health and graph availability"
    )
    _add_client_options(doctor_parser, include_scope=False, include_json=True)

    serve_parser = subparsers.add_parser(
        "serve", help="Start the local SEOCHO docker stack"
    )
    serve_parser.add_argument(
        "--project-dir", default=None, help="Repository root containing compose.yaml"
    )
    serve_parser.add_argument(
        "--build", action="store_true", help="Rebuild images before starting"
    )
    serve_parser.add_argument(
        "--no-wait", action="store_true", help="Return after docker compose starts"
    )
    serve_parser.add_argument(
        "--timeout", type=float, default=90.0, help="Readiness wait timeout in seconds"
    )
    serve_parser.add_argument(
        "--instance",
        default=None,
        help="Boot an isolated per-worktree app tier (offset ports + ephemeral DB) against the shared neo4j",
    )
    serve_parser.add_argument(
        "--fallback-openai-key",
        default="dummy-key",
        help="Fallback OPENAI_API_KEY for local verification when no key is set",
    )
    serve_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the compose command without running it",
    )
    serve_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="Emit JSON output"
    )

    stop_parser = subparsers.add_parser(
        "stop", help="Stop the local SEOCHO docker stack"
    )
    stop_parser.add_argument(
        "--project-dir", default=None, help="Repository root containing compose.yaml"
    )
    stop_parser.add_argument(
        "--volumes", action="store_true", help="Also remove compose volumes"
    )
    stop_parser.add_argument(
        "--instance",
        default=None,
        help="Tear down a per-worktree app tier and drop only its ephemeral DB",
    )
    stop_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the compose command without running it",
    )
    stop_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="Emit JSON output"
    )

    artifacts_parser = subparsers.add_parser(
        "artifacts", help="Manage semantic artifacts"
    )
    artifact_subparsers = artifacts_parser.add_subparsers(
        dest="artifact_command", required=True
    )

    artifacts_list_parser = artifact_subparsers.add_parser(
        "list", help="List semantic artifacts"
    )
    artifacts_list_parser.add_argument(
        "--status", choices=["draft", "approved", "deprecated"], default=None
    )
    _add_client_options(artifacts_list_parser, include_scope=False, include_json=True)

    artifacts_get_parser = artifact_subparsers.add_parser(
        "get", help="Read one semantic artifact"
    )
    artifacts_get_parser.add_argument(
        "artifact_id", help="Semantic artifact identifier"
    )
    _add_client_options(artifacts_get_parser, include_scope=False, include_json=True)

    artifacts_create_parser = artifact_subparsers.add_parser(
        "create-draft", help="Create a draft semantic artifact"
    )
    artifacts_create_parser.add_argument(
        "--artifact-file", required=True, help="Path to artifact JSON payload"
    )
    artifacts_create_parser.add_argument(
        "--name", default=None, help="Override artifact name"
    )
    _add_client_options(artifacts_create_parser, include_scope=False, include_json=True)

    artifacts_approve_parser = artifact_subparsers.add_parser(
        "approve", help="Approve a draft semantic artifact"
    )
    artifacts_approve_parser.add_argument(
        "artifact_id", help="Semantic artifact identifier"
    )
    artifacts_approve_parser.add_argument(
        "--approved-by", required=True, help="Reviewer identifier"
    )
    artifacts_approve_parser.add_argument(
        "--approval-note", default=None, help="Approval note"
    )
    _add_client_options(
        artifacts_approve_parser, include_scope=False, include_json=True
    )

    artifacts_deprecate_parser = artifact_subparsers.add_parser(
        "deprecate", help="Deprecate an approved semantic artifact"
    )
    artifacts_deprecate_parser.add_argument(
        "artifact_id", help="Semantic artifact identifier"
    )
    artifacts_deprecate_parser.add_argument(
        "--deprecated-by", required=True, help="Reviewer identifier"
    )
    artifacts_deprecate_parser.add_argument(
        "--deprecation-note", default=None, help="Deprecation note"
    )
    _add_client_options(
        artifacts_deprecate_parser, include_scope=False, include_json=True
    )

    artifacts_validate_parser = artifact_subparsers.add_parser(
        "validate", help="Validate one artifact payload"
    )
    validate_source_group = artifacts_validate_parser.add_mutually_exclusive_group(
        required=True
    )
    validate_source_group.add_argument(
        "--artifact-id", dest="artifact_id", help="Semantic artifact identifier"
    )
    validate_source_group.add_argument(
        "--artifact-file", dest="artifact_file", help="Artifact JSON payload path"
    )
    _add_client_options(
        artifacts_validate_parser, include_scope=False, include_json=True
    )

    artifacts_diff_parser = artifact_subparsers.add_parser(
        "diff", help="Diff two artifact payloads"
    )
    left_group = artifacts_diff_parser.add_mutually_exclusive_group(required=True)
    left_group.add_argument(
        "--left-artifact-id", dest="left_artifact_id", help="Left artifact identifier"
    )
    left_group.add_argument(
        "--left-artifact-file",
        dest="left_artifact_file",
        help="Left artifact JSON payload path",
    )
    right_group = artifacts_diff_parser.add_mutually_exclusive_group(required=True)
    right_group.add_argument(
        "--right-artifact-id",
        dest="right_artifact_id",
        help="Right artifact identifier",
    )
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
    artifacts_apply_parser.add_argument(
        "artifact_id", help="Approved semantic artifact identifier"
    )
    artifacts_apply_parser.add_argument("content", help="Memory text to store")
    artifacts_apply_parser.add_argument("--metadata", help="JSON metadata object")
    artifacts_apply_parser.add_argument(
        "--prompt-context", help="JSON semantic prompt context override"
    )
    artifacts_apply_parser.add_argument("--database", help="Target database override")
    artifacts_apply_parser.add_argument(
        "--category", default="memory", help="Document category"
    )
    artifacts_apply_parser.add_argument(
        "--source-type", default="text", help="Source type: text, csv, or pdf"
    )
    _add_client_options(artifacts_apply_parser, include_scope=True, include_json=True)

    # --- Local-mode commands (no server needed) ---


def register_local_commands(subparsers: Any) -> None:
    init_parser = subparsers.add_parser(
        "init", help="Create a new ontology interactively"
    )
    init_parser.add_argument(
        "--output", default="schema.jsonld", help="Output file (default: schema.jsonld)"
    )
    init_parser.add_argument(
        "--format", choices=["jsonld", "yaml"], default="jsonld", help="Output format"
    )

    index_parser = subparsers.add_parser(
        "index", help="Index files from a directory into the graph"
    )
    index_parser.add_argument("path", help="File or directory to index")
    index_parser.add_argument("--database", default="neo4j", help="Target database")
    index_parser.add_argument(
        "--schema",
        default="schema.jsonld",
        help="Ontology file (JSON-LD, YAML, or TTL)",
    )
    index_parser.add_argument(
        "--neo4j-uri", default="bolt://localhost:7687", help="Neo4j/DozerDB URI"
    )
    index_parser.add_argument("--neo4j-user", default="neo4j", help="Neo4j user")
    index_parser.add_argument(
        "--neo4j-password", default="password", help="Neo4j password"
    )
    index_parser.add_argument(
        "--provider",
        choices=LOCAL_PROVIDERS,
        default="mara",
        help="OpenAI-compatible LLM provider preset",
    )
    index_parser.add_argument("--model", default=None, help="LLM model for extraction")
    index_parser.add_argument(
        "--llm-base-url", default=None, help="Override the provider base URL"
    )
    index_parser.add_argument(
        "--force", action="store_true", help="Re-index even if unchanged"
    )
    index_parser.add_argument(
        "--recursive", action="store_true", default=True, help="Scan subdirectories"
    )
    index_parser.add_argument(
        "--strict", action="store_true", help="Reject data that fails SHACL validation"
    )
    index_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="JSON output"
    )

    local_ask_parser = subparsers.add_parser(
        "local-ask", help="Ask a question against local graph (no server)"
    )
    local_ask_parser.add_argument("question", help="Question to ask")
    local_ask_parser.add_argument("--database", default="neo4j", help="Target database")
    local_ask_parser.add_argument(
        "--schema",
        default="schema.jsonld",
        help="Ontology file (JSON-LD, YAML, or TTL)",
    )
    local_ask_parser.add_argument(
        "--neo4j-uri", default="bolt://localhost:7687", help="Neo4j URI"
    )
    local_ask_parser.add_argument("--neo4j-user", default="neo4j", help="Neo4j user")
    local_ask_parser.add_argument(
        "--neo4j-password", default="password", help="Neo4j password"
    )
    local_ask_parser.add_argument(
        "--provider",
        choices=LOCAL_PROVIDERS,
        default="mara",
        help="OpenAI-compatible LLM provider preset",
    )
    local_ask_parser.add_argument("--model", default=None, help="LLM model")
    local_ask_parser.add_argument(
        "--llm-base-url", default=None, help="Override the provider base URL"
    )
    local_ask_parser.add_argument(
        "--reasoning", action="store_true", help="Enable reasoning mode (auto-retry)"
    )
    local_ask_parser.add_argument(
        "--repair-budget", type=int, default=2, help="Max repair attempts"
    )
    local_ask_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="JSON output"
    )

    status_parser = subparsers.add_parser("status", help="Show graph database status")
    status_parser.add_argument("--database", default="neo4j", help="Target database")
    status_parser.add_argument(
        "--schema",
        default="schema.jsonld",
        help="Ontology file (JSON-LD, YAML, or TTL)",
    )
    status_parser.add_argument(
        "--neo4j-uri", default="bolt://localhost:7687", help="Neo4j URI"
    )
    status_parser.add_argument("--neo4j-user", default="neo4j", help="Neo4j user")
    status_parser.add_argument(
        "--neo4j-password", default="password", help="Neo4j password"
    )
    status_parser.add_argument(
        "--provider",
        choices=LOCAL_PROVIDERS,
        default="mara",
        help="OpenAI-compatible LLM provider preset",
    )
    status_parser.add_argument(
        "--model", default=None, help="LLM model used for local queries"
    )
    status_parser.add_argument(
        "--llm-base-url", default=None, help="Override the provider base URL"
    )
    status_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="JSON output"
    )

    compare_parser = subparsers.add_parser(
        "compare", help="Compare two configs/models side by side"
    )
    compare_parser.add_argument(
        "input_text", help="Text to extract from (or file path with @)"
    )
    compare_parser.add_argument(
        "--config-a", required=True, help="First ontology file (JSON-LD or YAML)"
    )
    compare_parser.add_argument(
        "--config-b", required=True, help="Second ontology file"
    )
    compare_parser.add_argument(
        "--model-a", default="gpt-4o", help="LLM model for config A"
    )
    compare_parser.add_argument(
        "--model-b", default=None, help="LLM model for config B (default: same as A)"
    )
    compare_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="JSON output"
    )

    experiment_parser = subparsers.add_parser(
        "experiment",
        help="Run extraction-only multi-axis exploration (full e2e variants: see seocho sweep)",
    )
    experiment_parser.add_argument(
        "--input", required=True, help="Input text, @file, or directory path"
    )
    experiment_parser.add_argument(
        "--ontology",
        action="append",
        default=[],
        help="Ontology files to vary (repeat for multiple)",
    )
    experiment_parser.add_argument(
        "--model", action="append", default=[], help="LLM models to vary"
    )
    experiment_parser.add_argument(
        "--chunk-size",
        type=int,
        action="append",
        default=[],
        dest="chunk_sizes",
        help="Chunk sizes to vary",
    )
    experiment_parser.add_argument(
        "--temperature",
        type=float,
        action="append",
        default=[],
        dest="temperatures",
        help="Temperatures to vary",
    )
    experiment_parser.add_argument(
        "--output", default=None, help="Save results to this directory"
    )
    experiment_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="JSON output"
    )

    bundle_parser = subparsers.add_parser(
        "bundle", help="Export or inspect portable runtime bundles"
    )
    bundle_subparsers = bundle_parser.add_subparsers(
        dest="bundle_command", required=True
    )

    bundle_export_parser = bundle_subparsers.add_parser(
        "export", help="Export a local SDK configuration as a portable bundle"
    )
    bundle_export_parser.add_argument(
        "--output", required=True, help="Output bundle JSON file"
    )
    bundle_export_parser.add_argument(
        "--app-name", default=None, help="Portable app name"
    )
    bundle_export_parser.add_argument(
        "--database", default="neo4j", help="Default database for the portable runtime"
    )
    bundle_export_parser.add_argument(
        "--schema",
        default="schema.jsonld",
        help="Ontology file (JSON-LD, YAML, or TTL)",
    )
    bundle_export_parser.add_argument(
        "--neo4j-uri", default="bolt://localhost:7687", help="Neo4j/DozerDB URI"
    )
    bundle_export_parser.add_argument(
        "--neo4j-user", default="neo4j", help="Neo4j user"
    )
    bundle_export_parser.add_argument(
        "--neo4j-password", default="password", help="Neo4j password"
    )
    bundle_export_parser.add_argument(
        "--provider",
        choices=LOCAL_PROVIDERS,
        default="mara",
        help="OpenAI-compatible LLM provider preset",
    )
    bundle_export_parser.add_argument("--model", default=None, help="LLM model")
    bundle_export_parser.add_argument(
        "--llm-base-url", default=None, help="Override the provider base URL"
    )
    bundle_export_parser.add_argument(
        "--prompt-preset",
        default=None,
        choices=[
            "general",
            "finance",
            "legal",
            "medical",
            "research",
            "rdf_general",
            "rdf_fibo",
        ],
        help="Optional extraction prompt preset to serialize into the portable bundle",
    )
    bundle_export_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="JSON output"
    )

    bundle_show_parser = bundle_subparsers.add_parser(
        "show", help="Show one portable runtime bundle"
    )
    bundle_show_parser.add_argument("bundle", help="Path to bundle JSON file")
    bundle_show_parser.add_argument(
        "--json", dest="output_json", action="store_true", help="JSON output"
    )

    serve_http_parser = subparsers.add_parser(
        "serve-http", help="Serve a portable bundle behind a small FastAPI runtime"
    )
    serve_http_parser.add_argument(
        "--bundle", required=True, help="Path to portable bundle JSON file"
    )
    serve_http_parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    serve_http_parser.add_argument("--port", type=int, default=8010, help="Bind port")
    serve_http_parser.add_argument(
        "--reload", action="store_true", help="Enable uvicorn reload mode"
    )
