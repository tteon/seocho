from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from ..client import Seocho
from ..exceptions import SeochoError
from ..governance import ArtifactDiff, ArtifactValidationResult
from ..local import LocalRuntimeStatus, serve_local_runtime, stop_local_runtime
from ..semantic import SemanticArtifact, SemanticArtifactSummary
from ..models import ArchiveResult, ChatResponse, GraphTarget, Memory, MemoryCreateResult, SearchResult


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("seocho")
    except Exception:
        return "unknown"


@dataclass(frozen=True)
class CommandGroup:
    """A pluggable top-level command group.

    This is the extension seam new command families (``seocho ont``,
    ``seocho policy``) attach to, instead of the four legacy edit sites
    (build_parser, LOCAL_COMMANDS, _dispatch_local, a _cmd_* if-chain).
    ``register`` receives the top-level subparsers object and builds the
    group's parser tree; ``handle`` receives the parsed namespace and returns
    an exit code. ``local`` marks groups that never need the HTTP client.
    Existing commands stay on the legacy dispatch and migrate group by group.
    """

    name: str
    register: Callable[[Any], None]
    handle: Callable[[argparse.Namespace], int]
    local: bool = True


COMMAND_GROUPS: Dict[str, CommandGroup] = {}


def register_group(group: CommandGroup) -> None:
    if group.name in COMMAND_GROUPS:
        raise ValueError(f"command group already registered: {group.name}")
    COMMAND_GROUPS[group.name] = group


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seocho", description="SEOCHO memory-first CLI")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {_package_version()}"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print full tracebacks instead of one-line error messages",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    from .parsers import register_memory_commands, register_local_commands

    register_memory_commands(subparsers)
    register_local_commands(subparsers)

    # Registered command groups build their parser trees last, so a group can
    # never shadow a legacy command (register_group refuses duplicate names).
    for group in COMMAND_GROUPS.values():
        group.register(subparsers)

    return parser


LOCAL_COMMANDS = {
    "init",
    "index",
    "local-ask",
    "status",
    "compare",
    "experiment",
    "bundle",
    "serve-http",
}


def _print_error(exc: BaseException, *, debug: bool) -> None:
    if debug:
        traceback.print_exc(file=sys.stderr)
    else:
        print(str(exc), file=sys.stderr)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    debug = bool(getattr(args, "debug", False))

    # CLI commands run in a fresh process, unlike the HTTP runtime which
    # configures tracing during application startup. Honor the same env
    # contract here so one-shot ``seocho run`` executions are observable.
    try:
        from ..tracing import configure_tracing_from_env

        configure_tracing_from_env()
    except Exception as exc:
        # Observability must not hide the command's primary outcome; a broken
        # exporter is surfaced by its own diagnostics and the command proceeds.
        if debug:
            print(f"Tracing configuration failed: {exc}", file=sys.stderr)

    group = COMMAND_GROUPS.get(args.command)
    if group is not None:
        try:
            return group.handle(args)
        except Exception as exc:
            _print_error(exc, debug=debug)
            return 1

    # Local-mode commands don't need HTTP client
    if args.command in LOCAL_COMMANDS:
        try:
            return _dispatch_local(args)
        except Exception as exc:
            _print_error(exc, debug=debug)
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
        _print_error(exc, debug=debug)
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
            build=args.build,
            wait=not args.no_wait,
            timeout=args.timeout,
            fallback_openai_key=args.fallback_openai_key,
            instance=args.instance,
            dry_run=args.dry_run,
        )
        _print_result(status, args.output_json)
        return 0

    if args.command == "stop":
        status = stop_local_runtime(
            project_dir=args.project_dir,
            volumes=args.volumes,
            instance=args.instance,
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


from .options import add_client_options as _add_client_options  # noqa: F401 - compatibility


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
        print("No memories found.")
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
        print("No graph targets configured.")
        return

    for graph in graph_list:
        description = f" - {graph.description}" if graph.description else ""
        print(f"{graph.graph_id} ({graph.database}){description}")


def _print_artifacts(artifacts: Sequence[SemanticArtifactSummary], output_json: bool) -> None:
    if output_json:
        print(json.dumps([artifact.to_dict() for artifact in artifacts], indent=2))
        return

    if not artifacts:
        print("No semantic artifacts found.")
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
    if args.command == "serve-http":
        return _cmd_serve_http(args)
    raise SeochoError(f"Unknown local command: {args.command}")


def _cmd_init(args: argparse.Namespace) -> int:
    """Interactive ontology creation."""
    from ..ontology import NodeDef, Ontology, P, RelDef

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
        props_input = input(f"  Properties for {label} (comma-separated, e.g. name, age, role): ").strip()
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
    from ..config_file import write_config
    config_path = Path(".seocho.toml")
    if not config_path.exists():
        write_config(config_path, schema=output, database=name)
        print("Project config saved to .seocho.toml")

    print(f"Ontology saved to {output}")
    print(f"  {len(nodes)} entity types, {len(relationships)} relationships")
    print()
    print("Next steps:")
    print("  seocho index ./your_data/")
    print("  seocho ask --local 'your question here'")
    return 0


def _load_local_ontology(schema_path: str) -> Any:
    """Load ontology from file."""
    from ..ontology import Ontology

    path = Path(schema_path)
    if not path.exists():
        raise SeochoError(f"Schema file not found: {schema_path}\nRun 'seocho init' to create one.")
    if path.suffix in (".yaml", ".yml"):
        return Ontology.from_yaml(path)
    return Ontology.from_jsonld(path)


def _build_local_client(args: argparse.Namespace) -> Seocho:
    """Build a local-mode Seocho client from CLI args + .seocho.toml defaults."""
    from ..config_file import get_default, load_config
    from ..query.strategy import PRESET_PROMPTS
    from ..store.graph import Neo4jGraphStore
    from ..store.llm import create_llm_backend

    cfg = load_config()

    schema = getattr(args, "schema", None) or get_default(cfg, "project", "schema", "schema.jsonld")
    neo4j_uri = getattr(args, "neo4j_uri", None) or get_default(cfg, "neo4j", "uri", "bolt://localhost:7687")
    neo4j_user = getattr(args, "neo4j_user", None) or get_default(cfg, "neo4j", "user", "neo4j")
    neo4j_password = getattr(args, "neo4j_password", None) or get_default(cfg, "neo4j", "password", "password")
    provider = getattr(args, "provider", None) or get_default(cfg, "llm", "provider", "mara")
    model = getattr(args, "model", None) or get_default(cfg, "llm", "model", None)
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
        if getattr(args, "output_json", False):
            print(json.dumps({"answer": answer}, indent=2))
        else:
            print(answer)
    finally:
        client.close()

    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Show graph database status."""
    from ..store.graph import Neo4jGraphStore

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
    from ..experiment import ExperimentRunner
    from ..store.llm import OpenAIBackend

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
    from ..experiment import Workbench

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
    from ..runtime_bundle import RuntimeBundle

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


def _cmd_serve_http(args: argparse.Namespace) -> int:
    from ..http_runtime import create_bundle_runtime_app
    from ..runtime_bundle import RuntimeBundle

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


# ----------------------------------------------------------------------
# Migrated command groups. Each registers here and disappears from the
# legacy dispatch above; the parser contract tests hold the tree steady
# while the remaining groups follow (seocho-vh0).
# ----------------------------------------------------------------------
from . import ontology as _ontology_group  # noqa: E402

register_group(
    CommandGroup(
        name="ontology",
        register=_ontology_group.register,
        handle=_ontology_group.handle,
    )
)

from . import runs as _runs_group  # noqa: E402

register_group(CommandGroup(name="runs", register=_runs_group.register, handle=_runs_group.handle))

from . import run as _run_group  # noqa: E402

register_group(CommandGroup(name="run", register=_run_group.register, handle=_run_group.handle))

_cmd_run = _run_group.handle

from . import sweep as _sweep_group  # noqa: E402

register_group(CommandGroup(name="sweep", register=_sweep_group.register, handle=_sweep_group.handle))

_cmd_sweep = _sweep_group.handle

from . import traces as _traces_group  # noqa: E402

register_group(CommandGroup(name="traces", register=_traces_group.register, handle=_traces_group.handle))

_cmd_traces = _traces_group.handle

from . import new as _new_group  # noqa: E402

register_group(CommandGroup(name="new", register=_new_group.register, handle=_new_group.handle))

_cmd_new = _new_group.handle

from . import connect as _connect_group  # noqa: E402

register_group(CommandGroup(name="connect", register=_connect_group.register, handle=_connect_group.handle))
register_group(CommandGroup(name="connectors", register=lambda subparsers: None, handle=_connect_group.handle))

_cmd_connect = _connect_group.handle

LOCAL_COMMANDS.update(name for name, group in COMMAND_GROUPS.items() if group.local)

if __name__ == "__main__":
    raise SystemExit(main())
