"""End-to-end runner behind ``seocho run``: build → index → query → report.

The builder turns a validated :class:`~seocho.run_spec.RunSpec` into live
SDK objects (ontology, graph store, one or two clients), and the runner
drives the three phases. Design vocabulary is delegated to
:class:`~seocho.agent_design.AgentDesignSpec` and
:class:`~seocho.indexing_design.IndexingDesignSpec`; this module only adds
run-scoped wiring.

When the indexing and query models differ, two clients are built sharing
one graph store, ontology, and workspace — per-phase model separation
without any per-call plumbing.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .run_spec import RunSpec, RunSpecError, load_run_spec, parse_model_ref
from .run_preflight import run_preflight

_BOLT_SCHEMES = ("bolt://", "neo4j://", "neo4j+s://", "bolt+s://")


@dataclass(slots=True)
class RunContext:
    """Live objects assembled from a run spec, ready to execute."""

    spec: RunSpec
    ontology: Any
    graph_store: Any
    index_client: Any
    query_client: Any
    database: str
    documents_path: Path
    output_dir: Path

    def close(self) -> None:
        for client in {id(self.index_client): self.index_client, id(self.query_client): self.query_client}.values():
            try:
                client.close()
            except Exception:
                pass


def _resolve(spec: RunSpec, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    base = Path(spec.source_path).parent if spec.source_path else Path(".")
    return base / path


def _load_design(spec: RunSpec, *, section: str) -> Optional[Any]:
    design = getattr(spec, section).get("design")
    if not design:
        return None
    if section == "agent":
        from .agent_design import AgentDesignSpec as design_cls
    else:
        from .indexing_design import IndexingDesignSpec as design_cls
    if isinstance(design, dict):
        return design_cls.from_dict(design)
    return design_cls.from_yaml(_resolve(spec, str(design)))


def build_agent_config(spec: RunSpec) -> Any:
    """Compile the run spec's agent/query sections into an AgentConfig.

    Precedence: agent-design pattern defaults < inline run-spec keys —
    the same layering AgentDesignSpec.to_agent_config() applies to its
    own sections.
    """
    from .agent_config import AgentConfig, RoutingPolicy

    agent_design = _load_design(spec, section="agent")
    config = agent_design.to_agent_config() if agent_design is not None else AgentConfig()

    overrides: Dict[str, Any] = {}
    execution_mode = str(spec.agent.get("execution_mode") or "").strip().lower()
    if execution_mode:
        overrides["execution_mode"] = execution_mode
        if execution_mode == "supervisor":
            overrides["handoff"] = True
    routing_policy = str(spec.agent.get("routing_policy") or "").strip().lower()
    if routing_policy:
        overrides["routing_policy"] = {
            "fast": RoutingPolicy.fast,
            "balanced": RoutingPolicy.balanced,
            "thorough": RoutingPolicy.thorough,
        }[routing_policy]()
    if "reasoning_mode" in spec.query:
        overrides["reasoning_mode"] = bool(spec.query["reasoning_mode"])
    if "repair_budget" in spec.query:
        overrides["repair_budget"] = int(spec.query["repair_budget"])
    if "answer_style" in spec.query:
        overrides["answer_style"] = str(spec.query["answer_style"]).strip().lower()
    # ontology.enforcement: an explicit run-spec value overrides the agent
    # design; the implicit "guided" default never does.
    if spec.enforcement_set or agent_design is None:
        overrides["ontology_enforcement"] = spec.enforcement
    effective_enforcement = overrides.get(
        "ontology_enforcement", getattr(config, "ontology_enforcement", "guided")
    )
    if effective_enforcement == "strict" and config.validation_on_fail == "warn":
        overrides["validation_on_fail"] = "reject"

    return dataclasses.replace(config, **overrides) if overrides else config


def _build_llm(model_ref: str) -> Any:
    from .store.llm import create_llm_backend

    errors: List[str] = []
    provider, model = parse_model_ref(model_ref, where="models", errors=errors)
    if errors:
        raise RunSpecError(errors)
    return create_llm_backend(provider=provider, model=model)


def _build_graph_store(spec: RunSpec, ontology: Any) -> Any:
    if spec.graph and spec.graph.startswith(_BOLT_SCHEMES):
        from .store.graph import Neo4jGraphStore

        return Neo4jGraphStore(spec.graph, spec.graph_user, spec.graph_password)
    from .store.graph import LadybugGraphStore

    store = LadybugGraphStore(spec.graph or ".seocho/local.lbug")
    try:
        store.ensure_constraints(ontology)
    except Exception:
        pass
    return store


def build(spec: RunSpec) -> RunContext:
    """Assemble live SDK objects from a validated run spec."""
    from .client import Seocho
    from .ontology import Ontology

    ontology = Ontology.load(_resolve(spec, spec.ontology_path))

    client_kwargs: Dict[str, Any] = {
        "ontology": ontology,
        "workspace_id": spec.resolved_workspace_id(),
        "agent_config": build_agent_config(spec),
    }
    indexing_design = _load_design(spec, section="indexing")
    if indexing_design is not None:
        design_kwargs = indexing_design.client_kwargs(ontology=ontology)
        client_kwargs.update({k: v for k, v in design_kwargs.items() if v is not None})
        ontology = client_kwargs["ontology"]

    graph_store = _build_graph_store(spec, ontology)
    client_kwargs["graph_store"] = graph_store

    index_client = Seocho(llm=_build_llm(spec.indexing_model()), **client_kwargs)
    if spec.uses_split_models():
        query_client = Seocho(llm=_build_llm(spec.query_model()), **client_kwargs)
    else:
        query_client = index_client

    database = spec.database or index_client.default_database
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = _resolve(spec, spec.output_dir) / f"{spec.name}-{timestamp}"

    return RunContext(
        spec=spec,
        ontology=ontology,
        graph_store=graph_store,
        index_client=index_client,
        query_client=query_client,
        database=database,
        documents_path=_resolve(spec, spec.documents_path),
        output_dir=output_dir,
    )


@dataclass(slots=True)
class RunReport:
    """Aggregated outcome of one e2e run."""

    payload: Dict[str, Any] = field(default_factory=dict)
    report_json: Optional[Path] = None
    report_md: Optional[Path] = None

    @property
    def ok(self) -> bool:
        indexing = self.payload.get("indexing") or {}
        queries = self.payload.get("queries") or []
        files_found = int(indexing.get("files_found", 0))
        files_failed = int(indexing.get("files_failed", 0))
        all_files_failed = files_found > 0 and files_failed >= files_found
        any_query_error = any(item.get("error") for item in queries)
        return not all_files_failed and not any_query_error


def _emit(quiet: bool, message: str = "") -> None:
    if not quiet:
        print(message)


def _run_index_phase(ctx: RunContext, *, force: bool, quiet: bool) -> Dict[str, Any]:
    spec = ctx.spec
    path = ctx.documents_path
    strict = spec.strict_validation()

    if path.is_file():
        result = ctx.index_client.index_file(
            str(path),
            database=ctx.database,
            category=str(spec.indexing.get("category") or "file"),
            force=force or bool(spec.indexing.get("force", False)),
            strict_validation=strict,
        )
        indexing = result.get("indexing") or {}
        summary = {
            "directory": str(path),
            "files_found": 1,
            "files_indexed": 1 if result.get("status") == "indexed" else 0,
            "files_skipped": 1 if result.get("status") == "skipped" else 0,
            "files_failed": 1 if result.get("status") == "failed" else 0,
            "files_unchanged": 1 if result.get("status") == "unchanged" else 0,
            "results": [result],
        }
    else:
        def _on_file(file_path: str, index: int, total: int) -> None:
            _emit(quiet, f"  [{index + 1}/{total}] {Path(file_path).name}")

        summary = ctx.index_client.index_directory(
            str(path),
            database=ctx.database,
            category=str(spec.indexing.get("category") or "file"),
            recursive=spec.documents_recursive,
            force=force or bool(spec.indexing.get("force", False)),
            on_file=None if quiet else _on_file,
            strict_validation=strict,
        )

    total_nodes = 0
    total_relationships = 0
    validation_errors: List[str] = []
    for file_result in summary.get("results", []):
        indexing = file_result.get("indexing") or {}
        total_nodes += int(indexing.get("total_nodes", 0))
        total_relationships += int(indexing.get("total_relationships", 0))
        validation_errors.extend(indexing.get("validation_errors", []) or [])
    summary["total_nodes"] = total_nodes
    summary["total_relationships"] = total_relationships
    summary["validation_errors_count"] = len(validation_errors)

    _emit(
        quiet,
        f"  indexed {summary.get('files_indexed', 0)}, "
        f"unchanged {summary.get('files_unchanged', 0)}, "
        f"failed {summary.get('files_failed', 0)} — "
        f"{total_nodes} nodes, {total_relationships} rels"
        + (f", {len(validation_errors)} validation errors" if validation_errors else ""),
    )
    return summary


def _run_query_phase(ctx: RunContext, *, quiet: bool) -> List[Dict[str, Any]]:
    spec = ctx.spec
    records: List[Dict[str, Any]] = []
    total = len(spec.questions)
    for index, question in enumerate(spec.questions):
        _emit(quiet, f"  [{index + 1}/{total}] {question.question}")
        record: Dict[str, Any] = {
            "id": question.question_id or str(index + 1),
            "question": question.question,
        }
        if question.expect:
            record["expect"] = question.expect
        started = time.monotonic()
        try:
            answer = ctx.query_client.ask(
                question.question,
                database=ctx.database,
                reasoning_mode=bool(spec.query.get("reasoning_mode", True)),
                repair_budget=int(spec.query.get("repair_budget", 1)),
                limit=int(spec.query.get("limit", 5)),
            )
        except Exception as exc:
            record["answer"] = ""
            record["error"] = str(exc)
            record["latency_s"] = round(time.monotonic() - started, 2)
            records.append(record)
            _emit(quiet, f"        -> ERROR: {exc}")
            continue
        record["answer"] = answer
        record["empty"] = not str(answer or "").strip()
        record["latency_s"] = round(time.monotonic() - started, 2)
        records.append(record)
        preview = str(answer or "").replace("\n", " ")
        if len(preview) > 100:
            preview = preview[:100] + "..."
        _emit(quiet, f"        -> {preview or '(empty)'}   ({record['latency_s']}s)")
    return records


def _render_report_md(payload: Dict[str, Any]) -> str:
    run = payload.get("run", {})
    indexing = payload.get("indexing", {})
    queries = payload.get("queries", [])
    lines = [
        f"# SEOCHO run: {run.get('name', '')}",
        "",
        f"- started: {run.get('started_at', '')}",
        f"- models: indexing={run.get('models', {}).get('indexing', '')}, "
        f"query={run.get('models', {}).get('query', '')}",
        f"- enforcement: {run.get('enforcement', '')}",
        f"- graph: {run.get('graph', 'embedded ladybug')} (database={run.get('database', '')})",
        "",
        "## Indexing",
        "",
        f"- files: {indexing.get('files_indexed', 0)} indexed, "
        f"{indexing.get('files_unchanged', 0)} unchanged, "
        f"{indexing.get('files_failed', 0)} failed",
        f"- graph: {indexing.get('total_nodes', 0)} nodes, "
        f"{indexing.get('total_relationships', 0)} relationships",
        f"- validation errors: {indexing.get('validation_errors_count', 0)}",
        "",
    ]
    if queries:
        lines += ["## Queries", "", "| # | question | answered | latency |", "|---|---|---|---|"]
        for item in queries:
            if item.get("error"):
                answered = "error"
            elif item.get("empty"):
                answered = "empty"
            else:
                answered = "yes"
            question_text = str(item.get("question", ""))
            if len(question_text) > 60:
                question_text = question_text[:60] + "..."
            lines.append(
                f"| {item.get('id', '')} | {question_text} | {answered} | {item.get('latency_s', '')}s |"
            )
        lines.append("")
        for item in queries:
            lines += [f"### Q{item.get('id', '')}: {item.get('question', '')}", ""]
            if item.get("expect"):
                lines += [f"**Expected:** {item['expect']}", ""]
            if item.get("error"):
                lines += [f"**Error:** {item['error']}", ""]
            else:
                lines += [str(item.get("answer", "")) or "_(empty answer)_", ""]
    else:
        lines += ["## Queries", "", "Index-only run (no questions declared).", ""]
    return "\n".join(lines)


def run(
    ctx: RunContext,
    *,
    only: Optional[str] = None,
    force: bool = False,
    quiet: bool = False,
) -> RunReport:
    """Execute the run: index → query → report. ``only`` limits to one phase."""
    spec = ctx.spec
    started_at = datetime.now().isoformat(timespec="seconds")
    payload: Dict[str, Any] = {
        "run": {
            "name": spec.name,
            "description": spec.description,
            "spec_path": spec.source_path,
            "started_at": started_at,
            "enforcement": spec.enforcement,
            "models": {"indexing": spec.indexing_model(), "query": spec.query_model()},
            "graph": spec.graph or "",
            "database": ctx.database,
            "workspace_id": spec.resolved_workspace_id(),
        }
    }

    phase_count = 2 if not only and not spec.index_only() else 1
    phase = 1
    durations: Dict[str, float] = {}

    if only in (None, "index"):
        _emit(quiet, f"Phase {phase}/{phase_count}: Indexing ({ctx.documents_path})")
        started = time.monotonic()
        payload["indexing"] = _run_index_phase(ctx, force=force, quiet=quiet)
        durations["index_s"] = round(time.monotonic() - started, 2)
        phase += 1
        _emit(quiet)

    if only in (None, "query") and not spec.index_only():
        mode = build_agent_config(spec).execution_mode
        _emit(quiet, f"Phase {phase}/{phase_count}: Querying ({len(spec.questions)} questions, mode={mode})")
        started = time.monotonic()
        payload["queries"] = _run_query_phase(ctx, quiet=quiet)
        durations["query_s"] = round(time.monotonic() - started, 2)
        _emit(quiet)

    payload["run"]["finished_at"] = datetime.now().isoformat(timespec="seconds")
    payload["run"]["durations"] = durations

    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    report_json = ctx.output_dir / "report.json"
    report_md = ctx.output_dir / "report.md"
    report_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    report_md.write_text(_render_report_md(payload), encoding="utf-8")

    if not quiet:
        _print_summary(payload)
        print(f"Report: {report_md}")
        print(f"        {report_json}")

    return RunReport(payload=payload, report_json=report_json, report_md=report_md)


def _print_summary(payload: Dict[str, Any]) -> None:
    indexing = payload.get("indexing")
    queries = payload.get("queries")
    print("Summary")
    if indexing:
        print(
            f"  index: {indexing.get('files_found', 0)} files, "
            f"{indexing.get('total_nodes', 0)} nodes, "
            f"{indexing.get('total_relationships', 0)} rels"
        )
    if queries is not None:
        answered = sum(1 for item in queries if not item.get("error") and not item.get("empty"))
        empty = sum(1 for item in queries if item.get("empty"))
        errored = sum(1 for item in queries if item.get("error"))
        line = f"  query: {answered}/{len(queries)} answered"
        if empty:
            line += f", {empty} empty"
        if errored:
            line += f", {errored} errors"
        print(line)
    print()


def run_from_config(
    config_path: "str | Path",
    *,
    dry_run: bool = False,
    only: Optional[str] = None,
    output_dir: Optional[str] = None,
    force: bool = False,
    json_output: bool = False,
) -> int:
    """CLI-facing orchestration: load spec → preflight → build → run.

    Exit codes: 0 ok, 1 runtime/preflight failure, 2 invalid config.
    """
    try:
        spec = load_run_spec(config_path)
    except RunSpecError as exc:
        count = len(exc.errors)
        print(f"{config_path}: {count} config error{'s' if count != 1 else ''}", file=sys.stderr)
        for error in exc.errors:
            print(f"  {error}", file=sys.stderr)
        return 2
    if output_dir:
        spec.output_dir = output_dir

    quiet = json_output
    _emit(quiet, f"SEOCHO run: {spec.name} ({config_path})")
    _emit(quiet, "=" * 70)
    _emit(quiet, "Preflight")
    report = run_preflight(spec, online=not dry_run)
    _emit(quiet, report.render())
    _emit(quiet)
    if not report.ok:
        if json_output:
            print(json.dumps({"ok": False, "preflight": [
                {"name": c.name, "status": c.status, "detail": c.detail} for c in report.checks
            ]}, ensure_ascii=False))
        else:
            print(f"Preflight failed ({len(report.failures())} checks). Nothing was run.", file=sys.stderr)
        return 1

    if dry_run:
        _emit(quiet, "Dry run: config valid, preflight passed. Resolved plan:")
        _emit(quiet, f"  models: indexing={spec.indexing_model()}, query={spec.query_model()}")
        _emit(quiet, f"  enforcement: {spec.enforcement} (strict_validation={spec.strict_validation()})")
        _emit(quiet, f"  graph: {spec.graph or 'embedded ladybug (.seocho/local.lbug)'}")
        _emit(quiet, f"  workspace: {spec.resolved_workspace_id()}")
        _emit(quiet, f"  questions: {len(spec.questions)}")
        if json_output:
            print(json.dumps({
                "ok": True,
                "dry_run": True,
                "models": {"indexing": spec.indexing_model(), "query": spec.query_model()},
                "enforcement": spec.enforcement,
                "questions": len(spec.questions),
            }, ensure_ascii=False))
        return 0

    ctx = build(spec)
    try:
        result = run(ctx, only=only, force=force, quiet=quiet)
    finally:
        ctx.close()

    if json_output:
        print(json.dumps(result.payload, indent=2, ensure_ascii=False))
    return 0 if result.ok else 1


__all__ = [
    "RunContext",
    "RunReport",
    "build",
    "build_agent_config",
    "run",
    "run_from_config",
]
