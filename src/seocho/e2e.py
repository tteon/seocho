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
import asyncio
import hashlib
import json
import logging
import re
import uuid
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from .run_clients import IndexClient, QueryClient
from .run_spec import RunSpec, RunSpecError, load_run_spec, parse_model_ref
from .run_evidence import collect_evidence, safe_endpoint
from .run_outcomes import RunDiagnostic, summarize_outcome
from .run_redaction import redact_diagnostic
from .run_preflight import run_preflight
from .run_reporting import FileReportStore, ReportStore, RunReport, render_report_md

# Compatibility for callers that used the former private renderer.
_render_report_md = render_report_md

if TYPE_CHECKING:
    from .ontology import Ontology
    from .store.graph import GraphStore

logger = logging.getLogger(__name__)

_BOLT_SCHEMES = ("bolt://", "neo4j://", "neo4j+s://", "bolt+s://")


@dataclass(slots=True)
class RunContext:
    """Live objects assembled from a run spec, ready to execute."""

    spec: RunSpec
    ontology: Ontology
    graph_store: GraphStore
    index_client: IndexClient
    query_client: QueryClient
    database: str
    documents_path: Path
    output_dir: Path

    resources: List[Any] = field(default_factory=list, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> List[Dict[str, str]]:
        if self._closed:
            return []
        self._closed = True
        return _close_resources(self.resources or [self.graph_store, self.index_client, self.query_client], self.spec)

    def __enter__(self) -> "RunContext":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _close_resources(resources: List[Any], spec: Optional[RunSpec] = None) -> List[Dict[str, str]]:
    errors: List[Dict[str, str]] = []
    seen: set[int] = set()
    for resource in reversed(resources):
        if id(resource) in seen:
            continue
        seen.add(id(resource))
        close = getattr(resource, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except Exception as exc:
            diagnostic = RunDiagnostic("cleanup", "resource_close_failed", redact_diagnostic(spec, exc),
                                       "Inspect resource shutdown before starting another run.")
            errors.append(diagnostic.to_dict())
            logger.warning("E2E cleanup failed for %s: %s", type(resource).__name__, redact_diagnostic(spec, exc))
    return errors


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
    # Backend selection only permits Bolt-compatible DozerDB / Neo4j stores.
    if spec.resolved_graph_kind() in ("neo4j", "dozerdb"):
        from .store.graph import Neo4jGraphStore

        return Neo4jGraphStore(spec.graph, spec.graph_user, spec.graph_password)
    raise RunSpecError(["graph.uri is required and must be a DozerDB/Neo4j Bolt URI."])


def _build_vector_store(spec: RunSpec) -> Optional[Any]:
    """Build the optional hybrid-search vector store from the ``vector:``
    section. Embedding defaults to local fastembed (bge) per the MARA-first
    policy; any other value is an LLM provider preset."""
    if not spec.uses_vector_store():
        return None
    from .store.vector import create_vector_store

    embedding = spec.vector_embedding()
    embedding_model = str(spec.vector.get("embedding_model") or "").strip()
    if embedding == "fastembed":
        from .store.fastembed_backend import make_fastembed_backend

        backend = (
            make_fastembed_backend(embedding_model)
            if embedding_model
            else make_fastembed_backend()
        )
        if backend is None:
            raise RuntimeError(
                "vector.embedding: fastembed is unavailable (pip install fastembed), "
                "or the bge model could not load. Alternatively set "
                "vector.embedding to an LLM provider preset (e.g. mara)."
            )
        # bge-small embeds at 384 dims; the factory default (1536) is the
        # OpenAI shape, so derive unless the spec pins one.
        dimension = int(spec.vector.get("dimension") or len(backend.embed(["probe"])[0]))
    else:
        from .store.llm import create_embedding_backend

        backend = create_embedding_backend(
            provider=embedding, model=embedding_model or None
        )
        dimension = int(spec.vector.get("dimension") or 1536)

    return create_vector_store(
        kind=spec.vector_kind(),
        embedding_backend=backend,
        dimension=dimension,
        uri=str(spec.vector.get("uri") or "./.lancedb"),
        table_name=str(spec.vector.get("table_name") or "seocho_vectors"),
    )


def resolve_guardrail(spec: RunSpec) -> RunSpec:
    """Domain-adaptively pick the guardrail ontology when ``ontology.select`` was
    declared (ADR-0123): score the candidates against the corpus profile and set
    ``spec.ontology_path`` to the chosen candidate, recording the recommendation
    on ``spec.selected_guardrail``. No-op when a fixed ``ontology.path`` is set."""
    if spec.ontology_path or not (spec.guardrail_candidates or spec.guardrail_fibo_catalog):
        return spec
    from .guardrail_selector import load_corpus_profile, select_guardrail
    from .ontology import Ontology

    corpus_profile = load_corpus_profile(_resolve(spec, spec.guardrail_corpus_profile))

    if spec.guardrail_fibo_catalog:
        # FIBO-catalog-derived candidates (ADR-0142): build bridged module
        # ontologies and pick. "stable" bridge derives a multi-model seed (needs a
        # provider); "lexical"/"none" stays offline via the fallback seed.
        from .fibo_catalog import select_fibo_guardrail
        backends = models = None
        if spec.guardrail_fibo_bridge == "stable":
            from .store.llm import create_llm_backend
            models = spec.guardrail_fibo_derive_models or [spec.indexing_model()]
            backends = [create_llm_backend(provider="mara", model=m) for m in models]
        extra = {name: Ontology.load(_resolve(spec, p)) for name, p in spec.guardrail_candidates.items()}
        rec, cands = select_fibo_guardrail(
            _resolve(spec, spec.guardrail_fibo_catalog), corpus_profile,
            modules=spec.guardrail_fibo_modules or None, backends=backends, models=models,
            collapse=True, extra_candidates=extra or None)
        spec.resolved_ontology = cands[rec.chosen]
        spec.selected_guardrail = rec.to_dict()
        return spec

    candidates = {name: Ontology.load(_resolve(spec, path))
                  for name, path in spec.guardrail_candidates.items()}
    rec = select_guardrail(candidates, corpus_profile)
    spec.ontology_path = spec.guardrail_candidates[rec.chosen]
    spec.selected_guardrail = rec.to_dict()
    return spec


def build(spec: RunSpec) -> RunContext:
    """Build with cleanup even when a later provider/client fails to initialize."""
    resources: List[Any] = []
    try:
        return _build(spec, resources)
    except BaseException:
        _close_resources(resources, spec)
        raise


def _build(spec: RunSpec, resources: List[Any]) -> RunContext:
    """Assemble live SDK objects from a validated run spec."""
    from .client import Seocho
    from .ontology import Ontology

    resolve_guardrail(spec)
    if spec.selected_guardrail:
        rec = spec.selected_guardrail
        where = spec.ontology_path or f"in-memory:{rec['chosen']}"
        logger.info("Selected guardrail %s (%s) for %s corpus", rec["chosen"], where, rec["domain_kind"])
        for advisory in rec.get("advisories", []):
            logger.info("Guardrail advisory: %s", advisory)

    # A FIBO-derived guardrail is resolved in-memory (no file); else load the path.
    ontology = spec.resolved_ontology if spec.resolved_ontology is not None else Ontology.load(_resolve(spec, spec.ontology_path))

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
    resources.append(graph_store)
    # All indexing paths share this adapter.  Attach the run-scoped projection
    # policy once so file, batch, and agent writes cannot silently disagree.
    setattr(graph_store, "_governance_mode", spec.governance_mode)
    if spec.governance_mode in {"governed", "lockdown"}:
        from .ontology.candidate_stage import GovernedCandidateStager
        import os
        cfg = spec.governance
        bundle_dir = str(cfg.get("bundle_dir") or "")
        state_db = str(cfg.get("state_db") or os.getenv("SEOCHO_ONTOLOGY_STATE_DB", ""))
        lease_id = str(cfg.get("lease_id") or os.getenv("SEOCHO_ONTOLOGY_LEASE_ID", ""))
        artifact_dir = str(cfg.get("artifact_dir") or (_resolve(spec, spec.output_dir) / "governance-candidates"))
        if not bundle_dir or not state_db or not lease_id:
            raise RunSpecError(["governed/lockdown requires governance.bundle_dir, state_db, and lease_id"])
        setattr(graph_store, "_governed_candidate_stager", GovernedCandidateStager(
            bundle_dir=bundle_dir, state_db=state_db, lease_id=lease_id, artifact_root=artifact_dir
        ))
    client_kwargs["graph_store"] = graph_store
    vector_store = _build_vector_store(spec)
    if vector_store is not None:
        client_kwargs["vector_store"] = vector_store

    index_llm = _build_llm(spec.indexing_model())
    resources.append(index_llm)
    index_client = Seocho(llm=index_llm, **client_kwargs)
    resources.append(index_client)
    if spec.uses_split_models():
        query_llm = _build_llm(spec.query_model())
        resources.append(query_llm)
        query_client = Seocho(llm=query_llm, **client_kwargs)
        resources.append(query_client)
    else:
        query_client = index_client

    database = spec.database or index_client.default_database
    output_dir = _run_directory(spec)

    return RunContext(
        spec=spec,
        ontology=ontology,
        graph_store=graph_store,
        index_client=index_client,
        query_client=query_client,
        database=database,
        documents_path=_resolve(spec, spec.documents_path),
        output_dir=output_dir,
        resources=resources,
    )


def _emit(quiet: bool, message: str = "") -> None:
    if not quiet:
        print(message)


def _to_plain_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        return dict(payload) if isinstance(payload, dict) else {}
    if dataclasses.is_dataclass(value):
        payload = dataclasses.asdict(value)
        return dict(payload) if isinstance(payload, dict) else {}
    return {}


def _first_mapping(*values: Any) -> Dict[str, Any]:
    for value in values:
        payload = _to_plain_dict(value)
        if payload and _mapping_has_signal(payload):
            return payload
    return {}


def _mapping_has_signal(payload: Dict[str, Any]) -> bool:
    for value in payload.values():
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, (int, float)) and value:
            return True
        if isinstance(value, (list, tuple, set)) and value:
            return True
        if isinstance(value, dict) and _mapping_has_signal(value):
            return True
    return False


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _populate_query_record(record: Dict[str, Any], response: Any) -> str:
    """Store the answer plus evidence-bearing response metadata when present."""
    answer = str(getattr(response, "response", response) or "")
    record["answer"] = answer

    runtime_mode = str(getattr(response, "runtime_mode", "") or "").strip()
    if runtime_mode:
        record["runtime_mode"] = runtime_mode

    envelope = _to_plain_dict(getattr(response, "answer_envelope", {}))
    if envelope:
        record["answer_envelope"] = envelope

    support = _first_mapping(
        getattr(response, "support", None),
        envelope.get("support_assessment") if envelope else {},
    )
    if support:
        record["support_assessment"] = support

    evidence = _first_mapping(
        getattr(response, "evidence", None),
        envelope.get("evidence_bundle") if envelope else {},
    )
    if evidence:
        record["evidence_bundle"] = evidence

    strategy = _first_mapping(
        getattr(response, "strategy", None),
        envelope.get("strategy_decision") if envelope else {},
    )
    if strategy:
        record["strategy_decision"] = strategy

    agent_pattern = _to_plain_dict(getattr(response, "agent_pattern", {}))
    if agent_pattern:
        record["agent_pattern"] = agent_pattern

    graph_cot = _to_plain_dict(getattr(response, "graph_cot", {}))
    if graph_cot:
        record["graph_cot"] = graph_cot

    question_frame = _to_plain_dict(getattr(response, "question_frame", {}))
    if question_frame:
        record["question_frame"] = question_frame

    routing_decision = _to_plain_dict(getattr(response, "routing_decision", {}))
    if routing_decision:
        record["routing_decision"] = routing_decision

    rewrite_trace = getattr(response, "rewrite_trace", None)
    if isinstance(rewrite_trace, list) and rewrite_trace:
        record["rewrite_trace"] = list(rewrite_trace)

    selected_triples = evidence.get("selected_triples", []) if evidence else []
    missing_slots = _string_list(
        evidence.get("missing_slots", []) if evidence else support.get("missing_slots", [])
    )
    support_status = (
        str(support.get("status", "") or "").strip()
        or str(strategy.get("support_status", "") or "").strip()
        or str(agent_pattern.get("support_status", "") or "").strip()
    )
    coverage = evidence.get("coverage") if evidence and "coverage" in evidence else support.get("coverage")
    intent_id = (
        str(evidence.get("intent_id", "") or "").strip()
        or str(support.get("intent_id", "") or "").strip()
    )

    if support_status:
        record["support_status"] = support_status
    if coverage is not None:
        record["coverage"] = coverage
    if intent_id:
        record["intent_id"] = intent_id
    if "missing_slots" in evidence or "missing_slots" in support:
        record["missing_slots"] = missing_slots
    if "selected_triples" in evidence and isinstance(selected_triples, list):
        record["selected_triple_count"] = len(selected_triples)

    return answer


def _run_index_phase(
    ctx: RunContext, *, force: bool, quiet: bool, track: bool = True
) -> Dict[str, Any]:
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
            track=track,
        )

    total_nodes = 0
    total_relationships = 0
    validation_errors: List[str] = []
    for file_result in summary.get("results", []):
        if file_result.get("error"):
            file_result["error"] = redact_diagnostic(spec, file_result["error"])
        indexing = file_result.get("indexing") or {}
        if indexing.get("fallback_reason"):
            indexing["fallback_reason"] = redact_diagnostic(spec, indexing["fallback_reason"])
        for error_field in ("write_errors", "validation_errors"):
            if indexing.get(error_field):
                indexing[error_field] = [redact_diagnostic(spec, error) for error in indexing[error_field]]
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


QueryCheckpoint = Callable[[List[Dict[str, Any]], Optional[Dict[str, Any]]], None]


def _run_query_phase(ctx: RunContext, *, quiet: bool, checkpoint: Optional[QueryCheckpoint] = None) -> List[Dict[str, Any]]:
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
        if checkpoint:
            checkpoint(records, record)
        started = time.monotonic()
        try:
            call_kwargs = {
                "database": ctx.database,
                "reasoning_mode": bool(spec.query.get("reasoning_mode", True)),
                "repair_budget": int(spec.query.get("repair_budget", 1)),
                "limit": int(spec.query.get("limit", 5)),
            }
            ask_response = getattr(ctx.query_client, "ask_response", None)
            if callable(ask_response):
                response = ask_response(question.question, **call_kwargs)
                answer = _populate_query_record(record, response)
            else:
                answer = ctx.query_client.ask(question.question, **call_kwargs)
                record["answer"] = answer
        except Exception as exc:
            record["answer"] = ""
            record["error"] = redact_diagnostic(spec, exc)
            record["diagnostic"] = RunDiagnostic("query", "query_failed", record["error"], "Inspect provider, query and evidence details; retry in a fresh run.", record["id"]).to_dict()
            record["latency_s"] = round(time.monotonic() - started, 2)
            records.append(record)
            if checkpoint:
                checkpoint(records, None)
            _emit(quiet, f"        -> ERROR: {record['error']}")
            continue
        record["empty"] = not str(answer or "").strip()
        record["latency_s"] = round(time.monotonic() - started, 2)
        records.append(record)
        if checkpoint:
            checkpoint(records, None)
        preview = str(answer or "").replace("\n", " ")
        if len(preview) > 100:
            preview = preview[:100] + "..."
        _emit(quiet, f"        -> {preview or '(empty)'}   ({record['latency_s']}s)")
    return records


def _agents_sdk_config(spec: RunSpec) -> tuple[str, int] | None:
    """Return the explicit J3 configuration; direct remains the default."""
    if str(spec.agent.get("runtime") or "").strip().lower() != "agents_sdk":
        return None
    bundle = str(spec.agent.get("ontology_bundle_dir") or "").strip()
    if not bundle:
        raise RunSpecError(["agent.runtime=agents_sdk requires agent.ontology_bundle_dir"])
    return bundle, max(1, int(spec.agent.get("max_turns") or 6))


def _run_agents_sdk_query_phase(ctx: RunContext, *, quiet: bool, bundle_dir: str, max_turns: int, checkpoint: Optional[QueryCheckpoint] = None) -> List[Dict[str, Any]]:
    """Actual ``Runner.run`` query path, kept separate from direct SEOCHO calls."""
    from .agents_runtime import get_agents_runtime
    from .integrations.openai_agents import build_graph_agent

    errors: List[str] = []
    provider, model = parse_model_ref(ctx.spec.query_model(), where="models.query", errors=errors)
    if errors or provider != "mara":
        raise RunSpecError(errors or ["Agents SDK experiment currently requires a mara query model"])
    agent = build_graph_agent(
        ontology=ctx.ontology, graph_store=ctx.graph_store, database=ctx.database,
        model=model, ontology_bundle_dir=str(_resolve(ctx.spec, bundle_dir)),
    )
    runtime = get_agents_runtime()
    records: List[Dict[str, Any]] = []
    for index, question in enumerate(ctx.spec.questions):
        record: Dict[str, Any] = {"id": question.question_id or str(index + 1), "question": question.question, "runtime_mode": "agents_sdk"}
        if question.expect:
            record["expect"] = question.expect
        if checkpoint:
            checkpoint(records, record)
        started = time.monotonic()
        try:
            with runtime.trace(f"seocho.j3.{record['id']}"):
                result = asyncio.run(runtime.run(agent=agent, input=question.question, max_turns=max_turns))
            record["answer"] = str(getattr(result, "final_output", result) or "")
            record["empty"] = not record["answer"].strip()
        except Exception as exc:
            record.update(answer="", error=redact_diagnostic(ctx.spec, exc), empty=True)
        record["latency_s"] = round(time.monotonic() - started, 2)
        records.append(record)
        if checkpoint:
            checkpoint(records, None)
        _emit(quiet, f"  [{index + 1}/{len(ctx.spec.questions)}] agents_sdk -> {'ERROR' if record.get('error') else 'ok'}")
    return records


def _execute_run(
    ctx: RunContext,
    *,
    only: Optional[str] = None,
    force: bool = False,
    quiet: bool = False,
    track: bool = True,
    payload: Dict[str, Any],
    store: ReportStore,
) -> RunReport:
    """Execute the run: index → query → report. ``only`` limits to one phase."""
    spec = ctx.spec
    from .ontology.plane_policy import decide_projection, projection_trace_receipt
    from .ontology.projection_receipt import (
        load_projection_admission_from_env,
        load_projection_receipt_from_env,
    )

    semantic_receipt = load_projection_receipt_from_env()
    admission = load_projection_admission_from_env()
    if spec.governance_mode in {"governed", "lockdown"} and admission is None:
        from .ontology.lifecycle import OntologyLifecycleStore

        state_db = str(spec.governance.get("state_db") or "").strip()
        lease_id = str(spec.governance.get("lease_id") or "").strip()
        if not state_db or not lease_id:
            raise RunSpecError(
                ["governed/lockdown requires governance.state_db and governance.lease_id"]
            )
        admission = OntologyLifecycleStore(state_db).admission(lease_id)
    staged_receipt = {"per_candidate": True} if spec.governance_mode in {"governed", "lockdown"} else None
    projection_decision = decide_projection(
        spec.governance_mode,
        rust_socket=os.environ.get("SEOCHO_RUST_PROJECTOR_SOCKET"),
        semantic_receipt=semantic_receipt or staged_receipt,
        admission=admission,
    )
    governance_receipt = projection_trace_receipt(
        projection_decision, semantic_receipt=semantic_receipt, admission=admission
    )
    started_at = datetime.now().isoformat(timespec="seconds")
    payload.update({
        "run": {
            "name": spec.name,
            "description": spec.description,
            "spec_path": spec.source_path,
            "started_at": started_at,
            "only": only,
            "question_count": len(spec.questions),
            "enforcement": spec.enforcement,
            "models": {"indexing": spec.indexing_model(), "query": spec.query_model()},
            "graph": safe_endpoint(spec.graph),
            "graph_kind": spec.resolved_graph_kind(),
            "vector": spec.vector_kind() if spec.uses_vector_store() else "",
            "database": ctx.database,
            "workspace_id": spec.resolved_workspace_id(),
            "governance_mode": spec.governance_mode,
            "projection_governance": governance_receipt,
        }
    })
    from .eval.experiment_observability import agents_sdk_runtime_receipt, direct_runtime_receipt, experiment_run_trace

    agents_config = _agents_sdk_config(spec)
    if agents_config:
        bundle_dir, max_turns = agents_config
        toolset_digest = hashlib.sha256(b"ontology_profile,ontology_slice,run_cypher").hexdigest()
        runtime_receipt = agents_sdk_runtime_receipt(max_turns=max_turns, toolset_digest=toolset_digest)
    else:
        runtime_receipt = direct_runtime_receipt()

    with experiment_run_trace(
        receipt={**runtime_receipt, "projection_governance": governance_receipt}, run_name=spec.name,
        workspace_id=spec.resolved_workspace_id(),
    ) as experiment_manifest:
        payload["run"]["experiment"] = experiment_manifest
        phase_count = 2 if not only and not spec.index_only() else 1
        phase = 1
        durations: Dict[str, float] = {}

        if only in (None, "index"):
            _emit(quiet, f"Phase {phase}/{phase_count}: Indexing ({ctx.documents_path})")
            started = time.monotonic()
            payload["active_stage"] = "index"
            store.write(payload)
            payload["indexing"] = _run_index_phase(ctx, force=force, quiet=quiet, track=track)
            for result in payload["indexing"].get("results", []):
                if result.get("status") in {"failed", "skipped"}:
                    detail = redact_diagnostic(spec, result.get("error") or "; ".join((result.get("indexing") or {}).get("write_errors", [])) or "Document produced no usable indexed content.")
                    payload["diagnostics"].append(RunDiagnostic(
                        "index", "document_" + result["status"], detail,
                        "Inspect this document's reader, extraction and graph-write result.",
                        str(result.get("path", "")),
                    ).to_dict())
            store.write(payload)
            durations["index_s"] = round(time.monotonic() - started, 2)
            phase += 1
            _emit(quiet)

        index = payload.get("indexing")
        query_allowed = index is None or int(index.get("files_indexed", 0)) + int(index.get("files_unchanged", 0)) > 0
        if not query_allowed and only != "index":
            payload["queries"] = [
                {"id": q.question_id or str(i + 1), "question": q.question,
                 "expect": q.expect, "answer": "", "skipped": True,
                 "error": "No usable documents; query was not executed."}
                for i, q in enumerate(spec.questions)
            ]
        if only in (None, "query") and not spec.index_only() and query_allowed:
            payload["active_stage"] = "query"
            store.write(payload)
            mode = build_agent_config(spec).execution_mode
            _emit(quiet, f"Phase {phase}/{phase_count}: Querying ({len(spec.questions)} questions, mode={mode})")
            started = time.monotonic()
            def checkpoint(records: List[Dict[str, Any]], active: Optional[Dict[str, Any]]) -> None:
                payload["queries"] = list(records)
                payload["active_question"] = dict(active) if active else None
                store.write(payload)

            payload["queries"] = (_run_agents_sdk_query_phase(ctx, quiet=quiet, bundle_dir=bundle_dir, max_turns=max_turns, checkpoint=checkpoint)
                                  if agents_config else _run_query_phase(ctx, quiet=quiet, checkpoint=checkpoint))
            durations["query_s"] = round(time.monotonic() - started, 2)
            payload["diagnostics"].extend(record["diagnostic"] for record in payload["queries"] if record.get("diagnostic"))
            store.write(payload)
            _emit(quiet)

        indexing_summary = payload.get("indexing") or {}
        query_records = payload.get("queries") or []
        if int(indexing_summary.get("files_failed", 0) or 0) or any(
            record.get("error") for record in query_records
        ):
            experiment_manifest["outcome"] = "partial"

        payload["run"]["finished_at"] = datetime.now().isoformat(timespec="seconds")
        payload["run"]["durations"] = durations
        # A single run produces a baseline. Cross-arm causal claims use the
        # explicit, conservative comparison helper rather than a blended score.
        from .eval.semantic_scorecard import score_semantic_utility

        candidates = [
            (item.get("indexing") or {}).get("governance_candidate")
            for item in (indexing_summary.get("results") or [])
            if (item.get("indexing") or {}).get("governance_candidate")
        ]
        governance = None
        if candidates:
            governance = {
                "promotable": all(bool(item.get("projection_receipt_sha256")) for item in candidates),
                "rdf_bundle_sha256": candidates[0].get("rdf_bundle_sha256"),
                "projection_receipt_sha256": candidates[0].get("projection_receipt_sha256"),
                "candidate_count": len(candidates),
            }
        payload["agent_scorecard"] = score_semantic_utility(
            payload.get("indexing"), payload.get("queries"), governance=governance,
        ).to_dict()

        payload["active_stage"] = "finished"
        payload["outcome"] = summarize_outcome(payload)
        result = store.write(payload)
        if not quiet:
            _print_summary(payload)
            print(f"Status: {payload['outcome']['status']}")
            print(f"Report: {result.report_md}")
            print(f"        {result.report_json}")
        return result


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


def _run_directory(spec: RunSpec) -> Path:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", spec.name).strip(".-") or "run"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return _resolve(spec, spec.output_dir) / f"{name}-{timestamp}-{uuid.uuid4().hex[:8]}"


def _initial_payload(spec: RunSpec) -> Dict[str, Any]:
    return {"schema_version": "seocho.run_report.v2",
            "run": {"name": spec.name, "workspace_id": spec.resolved_workspace_id(),
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "question_count": len(spec.questions)},
            "outcome": {"status": "running", "reasons": []},
            "diagnostics": [], "active_stage": "build"}


def _record_failure(payload: Dict[str, Any], exc: BaseException, store: ReportStore, spec: RunSpec) -> RunReport:
    interrupted = isinstance(exc, (KeyboardInterrupt, SystemExit))
    stage = payload.get("active_stage", "build")
    diagnostic = {"stage": stage, "code": "interrupted" if interrupted else f"{stage}_failed",
                  "message": redact_diagnostic(spec, exc) or type(exc).__name__,
                  "action": "Inspect the failed stage; graph writes may be partial. Retry with a fresh output directory."}
    payload["fatal_error"] = diagnostic
    payload["diagnostics"].append(diagnostic)
    payload["outcome"] = {"status": "interrupted" if interrupted else "failed", "reasons": [diagnostic["code"]]}
    payload["run"]["finished_at"] = datetime.now().isoformat(timespec="seconds")
    return store.write(payload)


def run(
    ctx: RunContext, *, only: Optional[str] = None, force: bool = False,
    quiet: bool = False, track: bool = True, store: Optional[ReportStore] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> RunReport:
    """Execute phases, preserving a checkpoint before every external stage."""
    store = store if store is not None else FileReportStore(ctx.output_dir)
    payload = payload if payload is not None else _initial_payload(ctx.spec)
    try:
        if "reproducibility" not in payload:
            payload["reproducibility"] = collect_evidence(ctx.spec, only=only, force=force, track=track)
        store.write(payload)
        _execute_run(ctx, only=only, force=force, quiet=quiet, track=track, payload=payload, store=store)
        return store.write(payload)
    except (KeyboardInterrupt, SystemExit) as exc:
        _record_failure(payload, exc, store, ctx.spec)
        raise
    except Exception as exc:
        return _record_failure(payload, exc, store, ctx.spec)


def run_spec_once(
    spec: RunSpec, *, only: Optional[str] = None, force: bool = False,
    quiet: bool = False, track: bool = True,
    output_dir_override: "Optional[str | Path]" = None,
) -> RunReport:
    """Build and run one spec; preserve failure receipts and release resources."""
    directory = Path(output_dir_override) if output_dir_override is not None else _run_directory(spec)
    store = FileReportStore(directory)
    payload = _initial_payload(spec)
    ctx: Optional[RunContext] = None
    try:
        payload["reproducibility"] = collect_evidence(spec, only=only, force=force, track=track)
        store.write(payload)
        ctx = build(spec)
        ctx.output_dir = directory
        result = run(ctx, only=only, force=force, quiet=quiet, track=track, store=store, payload=payload)
    except (KeyboardInterrupt, SystemExit) as exc:
        _record_failure(payload, exc, store, spec)
        raise
    except Exception as exc:
        result = _record_failure(payload, exc, store, spec)
    finally:
        if ctx is not None:
            errors = ctx.close()
            if errors:
                payload["cleanup_errors"] = errors
                payload["diagnostics"].extend(errors)
                if payload["outcome"]["status"] != "interrupted":
                    payload["outcome"] = summarize_outcome(payload)
                store.write(payload)
    if not quiet and not result.ok:
        print(f"Run {payload['outcome']['status']}. Inspect {result.report_md}", file=sys.stderr)
    return result


def _preflight_failure(
    spec: RunSpec, preflight: Any, *, persist: bool = True,
    directory: Optional[Path] = None,
) -> RunReport:
    payload = _initial_payload(spec)
    payload.update(ok=False, active_stage="preflight",
                   outcome={"status": "failed", "reasons": ["preflight_failed"]},
                   preflight=[{"name": c.name, "status": c.status, "detail": redact_diagnostic(spec, c.detail), "fix": redact_diagnostic(spec, c.fix)}
                              for c in preflight.checks])
    payload["diagnostics"] = [RunDiagnostic("preflight", "preflight_failed", redact_diagnostic(spec, c.detail),
                                            redact_diagnostic(spec, c.fix) or "Correct this check before retrying.", c.name).to_dict()
                              for c in preflight.failures()]
    if not persist:
        return RunReport(payload=payload)
    store = FileReportStore(directory if directory is not None else _run_directory(spec))
    payload["report_directory"] = str(store.directory)
    return store.write(payload)


def _print_config_errors(config_path: Any, errors: List[str]) -> None:
    count = len(errors)
    print(f"{config_path}: {count} config error{'s' if count != 1 else ''}", file=sys.stderr)
    for error in errors:
        print(f"  {error}", file=sys.stderr)


def run_from_config(
    config_path: "str | Path",
    *,
    dry_run: bool = False,
    only: Optional[str] = None,
    output_dir: Optional[str] = None,
    force: bool = False,
    track: bool = True,
    json_output: bool = False,
    vars_files: Optional[List[str]] = None,
    var_flags: Optional[List[str]] = None,
    show_rendered: bool = False,
) -> int:
    """CLI-facing orchestration: load spec → preflight → build → run.

    ``*.j2`` configs are rendered with Jinja2 (variables from ``--vars``
    files and ``--var`` flags) before parsing; plain YAML configs never
    touch the template layer, and supplying vars for one is an error.

    Exit codes: 0 ok, 1 runtime/preflight failure, 2 invalid config.
    """
    from .run_template import collect_cli_vars, is_template_path, load_templated_run_spec

    rendered_text: Optional[str] = None
    try:
        cli_vars = collect_cli_vars(vars_files, var_flags)
        if is_template_path(str(config_path)):
            spec, rendered_text = load_templated_run_spec(config_path, cli_vars)
        else:
            if cli_vars:
                raise RunSpecError(
                    [
                        "--var/--vars require a Jinja2 template; "
                        f"{config_path} is not a .j2 file. Rename it to "
                        "<name>.yaml.j2 to opt into templating."
                    ]
                )
            if show_rendered:
                rendered_text = Path(config_path).read_text(encoding="utf-8")
            spec = load_run_spec(config_path)
    except RunSpecError as exc:
        _print_config_errors(config_path, exc.errors)
        return 2
    if show_rendered:
        print(rendered_text, end="" if str(rendered_text).endswith("\n") else "\n")
        return 0
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
        failure = _preflight_failure(spec, report, persist=not dry_run)
        if json_output:
            print(json.dumps(failure.payload, ensure_ascii=False))
        else:
            print(f"Preflight failed ({len(report.failures())} checks). Nothing was run.", file=sys.stderr)
            if failure.report_md:
                print(f"Report: {failure.report_md}", file=sys.stderr)
        return 1

    if dry_run:
        _emit(quiet, "Dry run: config valid, preflight passed. Resolved plan:")
        _emit(quiet, f"  models: indexing={spec.indexing_model()}, query={spec.query_model()}")
        _emit(quiet, f"  enforcement: {spec.enforcement} (strict_validation={spec.strict_validation()})")
        _emit(quiet, f"  graph: {spec.resolved_graph_kind()} "
                     f"({spec.graph or 'missing Bolt URI'})")
        if spec.uses_vector_store():
            _emit(quiet, f"  vector: {spec.vector_kind()} (embedding={spec.vector_embedding()})")
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

    result = run_spec_once(spec, only=only, force=force, quiet=quiet, track=track)

    if json_output:
        print(json.dumps(result.payload, indent=2, ensure_ascii=False))
    return 0 if result.ok else 1


# ---------------------------------------------------------------------------
# Sweep: one template × N variants → N runs → one comparison summary
# ---------------------------------------------------------------------------


def _sweep_row(variant_name: str, status: str, payload: Optional[Dict[str, Any]] = None,
               detail: str = "") -> Dict[str, Any]:
    row: Dict[str, Any] = {"variant": variant_name, "status": status}
    if detail:
        row["detail"] = detail
    if not payload:
        return row
    indexing = payload.get("indexing") or {}
    queries = payload.get("queries")
    durations = (payload.get("run") or {}).get("durations") or {}
    row.update(
        {
            "files_found": int(indexing.get("files_found", 0)),
            "files_indexed": int(indexing.get("files_indexed", 0)),
            "files_failed": int(indexing.get("files_failed", 0)),
            "nodes": int(indexing.get("total_nodes", 0)),
            "rels": int(indexing.get("total_relationships", 0)),
            "validation_errors": int(indexing.get("validation_errors_count", 0)),
            "index_s": durations.get("index_s"),
            "query_s": durations.get("query_s"),
        }
    )
    if queries is not None:
        row["questions"] = len(queries)
        row["answered"] = sum(
            1 for item in queries if not item.get("error") and not item.get("empty")
        )
        row["empty"] = sum(1 for item in queries if item.get("empty"))
        row["query_errors"] = sum(1 for item in queries if item.get("error"))
    return row


def _format_sweep_table(rows: List[Dict[str, Any]]) -> List[str]:
    headers = ("variant", "files", "nodes", "rels", "answered", "empty", "errors",
               "index_s", "query_s", "")
    table_rows: List[Tuple[str, ...]] = []
    for row in rows:
        if row.get("files_found") is not None and row["status"] in ("ok", "failed"):
            answered = (
                f"{row.get('answered', 0)}/{row.get('questions', 0)}"
                if row.get("questions") is not None
                else "-"
            )
            table_rows.append(
                (
                    str(row["variant"]),
                    f"{row.get('files_indexed', 0)}/{row.get('files_found', 0)}",
                    str(row.get("nodes", 0)),
                    str(row.get("rels", 0)),
                    answered,
                    str(row.get("empty", 0)),
                    str(row.get("query_errors", 0)),
                    str(row.get("index_s", "-")),
                    str(row.get("query_s", "-")),
                    "" if row["status"] == "ok" else "FAILED",
                )
            )
        else:
            table_rows.append(
                (str(row["variant"]), "-", "-", "-", "-", "-", "-", "-", "-",
                 row["status"].upper())
            )
    widths = [
        max(len(headers[col]), *(len(item[col]) for item in table_rows))
        for col in range(len(headers))
    ]
    lines = [
        "  " + "  ".join(headers[col].ljust(widths[col]) for col in range(len(headers))).rstrip(),
        "  " + "─" * (sum(widths) + 2 * (len(headers) - 1)),
    ]
    for item in table_rows:
        lines.append(
            "  " + "  ".join(item[col].ljust(widths[col]) for col in range(len(headers))).rstrip()
        )
    return lines


def _render_sweep_summary_md(sweep_payload: Dict[str, Any]) -> str:
    info = sweep_payload.get("sweep", {})
    rows = sweep_payload.get("variants", [])
    lines = [
        f"# SEOCHO sweep: {info.get('name', '')}",
        "",
        f"- template: {info.get('template', '')}",
        f"- started: {info.get('started_at', '')}",
        f"- variants: {len(rows)}",
        "",
        "| variant | status | files | nodes | rels | answered | empty | errors | index_s | query_s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        answered = (
            f"{row.get('answered', 0)}/{row.get('questions', 0)}"
            if row.get("questions") is not None
            else "-"
        )
        lines.append(
            f"| {row['variant']} | {row['status']} "
            f"| {row.get('files_indexed', '-')}/{row.get('files_found', '-')} "
            f"| {row.get('nodes', '-')} | {row.get('rels', '-')} "
            f"| {answered} | {row.get('empty', '-')} | {row.get('query_errors', '-')} "
            f"| {row.get('index_s', '-')} | {row.get('query_s', '-')} |"
        )
    lines += [
        "",
        "Per-variant artifacts: `<variant>/report.md`, `<variant>/report.json`, "
        "`<variant>/rendered.yaml` (reproduce standalone with `seocho run rendered.yaml`).",
        "",
    ]
    return "\n".join(lines)


def run_sweep_from_config(
    config_path: "str | Path",
    *,
    vars_files: Optional[List[str]] = None,
    var_flags: Optional[List[str]] = None,
    dry_run: bool = False,
    only_variants: Optional[List[str]] = None,
    fail_fast: bool = False,
    output_dir: Optional[str] = None,
    force: bool = False,
    json_output: bool = False,
    show_rendered: Optional[str] = None,
) -> int:
    """Execute a sweep: render every variant up front (config errors are
    collected across ALL variants and nothing runs — exit 2), then run
    variants sequentially with per-variant isolation, keep-going by
    default (``fail_fast`` stops at the first failure — exit 1 if any
    variant failed), and write ``summary.json``/``summary.md``.
    """
    from .run_template import (
        absolutized_rendered_text,
        collect_cli_vars,
        derive_variant_isolation,
        load_sweep_spec,
        parse_rendered_run_spec,
        render_run_template,
    )

    try:
        sweep = load_sweep_spec(config_path)
        cli_vars = collect_cli_vars(vars_files, var_flags)
    except RunSpecError as exc:
        _print_config_errors(config_path, exc.errors)
        return 2
    if output_dir:
        sweep.output_dir = output_dir

    template_path = sweep.template_path()
    if not template_path.exists():
        _print_config_errors(config_path, [f"at template: {template_path} does not exist."])
        return 2
    template_text = template_path.read_text(encoding="utf-8")

    selected = sweep.variants
    if only_variants:
        known = {variant.name for variant in sweep.variants}
        unknown = [name for name in only_variants if name not in known]
        if unknown:
            _print_config_errors(
                config_path,
                [
                    f"at --only-variant: unknown variant(s) {', '.join(unknown)}. "
                    f"Declared: {', '.join(sorted(known))}."
                ],
            )
            return 2
        selected = [variant for variant in sweep.variants if variant.name in set(only_variants)]

    base_dir = Path(sweep.source_path).parent if sweep.source_path else Path(".")
    output_root = Path(sweep.output_dir)
    if not output_root.is_absolute():
        output_root = base_dir / output_root
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    sweep_run_dir = output_root / f"{sweep.name}-{timestamp}-{uuid.uuid4().hex[:8]}"

    # --- Stage 1: render + validate + derive isolation for EVERY variant.
    prepared: List[Tuple[Any, RunSpec, str]] = []
    stage1_errors: List[str] = []
    for index, variant in enumerate(selected):
        variables = sweep.variant_variables(variant, index, cli_vars)
        source = f"{template_path} (variant {variant.name})"
        try:
            rendered = render_run_template(template_text, variables, source=source)
            spec = parse_rendered_run_spec(rendered, source=str(template_path))
            derive_variant_isolation(
                spec, variant_name=variant.name, sweep_run_dir=sweep_run_dir
            )
            prepared.append((variant, spec, rendered))
        except RunSpecError as exc:
            stage1_errors.extend(f"variant {variant.name}: {error}" for error in exc.errors)
    if stage1_errors:
        _print_config_errors(config_path, stage1_errors)
        return 2

    if show_rendered is not None:
        for variant, _spec, rendered in prepared:
            if show_rendered not in ("", variant.name):
                continue
            print(f"# ─── variant: {variant.name} " + "─" * 40)
            print(rendered, end="" if rendered.endswith("\n") else "\n")
        return 0

    quiet = json_output
    _emit(quiet, f"SEOCHO sweep: {sweep.name} ({config_path})")
    _emit(quiet, "=" * 70)
    _emit(
        quiet,
        f"Template: {sweep.template} — {len(prepared)} variants: "
        + ", ".join(variant.name for variant, _s, _r in prepared),
    )
    _emit(quiet)

    if dry_run:
        any_failed = False
        for variant, spec, _rendered in prepared:
            _emit(quiet, f"[{variant.name}]")
            report = run_preflight(spec, online=False)
            _emit(quiet, report.render())
            _emit(quiet, f"  plan  models={spec.indexing_model()}/{spec.query_model()} "
                         f"enforcement={spec.enforcement} workspace={spec.resolved_workspace_id()}")
            _emit(quiet, f"        graph={spec.graph}")
            _emit(quiet)
            any_failed = any_failed or not report.ok
        if json_output:
            print(json.dumps({"ok": not any_failed, "dry_run": True,
                              "variants": [v.name for v, _s, _r in prepared]},
                             ensure_ascii=False))
        return 1 if any_failed else 0

    # Shared-server note: bolt variants coexist on one server — isolation
    # rides on per-variant database/workspace only (panel-recommended warning).
    bolt_variants = [v.name for v, _s, _r in prepared]
    if len(bolt_variants) > 1:
        _emit(
            quiet,
            "note: variants "
            + ", ".join(bolt_variants)
            + " share one graph server — isolation relies on per-variant "
            "database/workspace_id; data coexists on the server.",
        )
        _emit(quiet)

    # --- Stage 2: sequential execution, keep-going by default.
    rows: List[Dict[str, Any]] = []
    failed: List[str] = []
    started_at = datetime.now().isoformat(timespec="seconds")
    for position, (variant, spec, rendered) in enumerate(prepared, start=1):
        _emit(quiet, f"[{position}/{len(prepared)}] {variant.name}")
        preflight = run_preflight(spec, online=True)
        if not preflight.ok:
            _preflight_failure(spec, preflight, directory=sweep_run_dir / variant.slug)
            _emit(quiet, preflight.render())
            _emit(quiet, f"  variant PREFLIGHT FAILED — "
                         f"{'stopping (--fail-fast)' if fail_fast else 'continuing'}")
            _emit(quiet)
            rows.append(_sweep_row(
                variant.name, "preflight_failed",
                detail="; ".join(check.detail for check in preflight.failures()),
            ))
            failed.append(variant.name)
            if fail_fast:
                break
            continue

        variant_dir = sweep_run_dir / variant.slug
        variant_dir.mkdir(parents=True, exist_ok=True)
        (variant_dir / "rendered.yaml").write_text(
            absolutized_rendered_text(
                rendered,
                template_path=template_path,
                provenance=(
                    f"rendered by seocho sweep from {template_path} "
                    f"(sweep {sweep.name}, variant {variant.name})"
                ),
            ),
            encoding="utf-8",
        )
        try:
            result = run_spec_once(
                spec, force=force, quiet=quiet, track=False,
                output_dir_override=variant_dir,
            )
            status = "ok" if result.ok else "failed"
            rows.append(_sweep_row(variant.name, status, result.payload))
        except Exception as exc:  # one broken variant must not sink the sweep
            status = "error"
            detail = redact_diagnostic(spec, exc)
            rows.append(_sweep_row(variant.name, "error", detail=detail))
            _emit(quiet, f"  variant ERROR: {detail}")
        if status != "ok":
            failed.append(variant.name)
            if fail_fast:
                _emit(quiet, "  stopping (--fail-fast)")
                _emit(quiet)
                break
        _emit(quiet)

    sweep_payload = {
        "sweep": {
            "name": sweep.name,
            "spec_path": str(config_path),
            "template": str(sweep.template),
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "failed_variants": failed,
        },
        "variants": rows,
    }
    sweep_run_dir.mkdir(parents=True, exist_ok=True)
    (sweep_run_dir / "summary.json").write_text(
        json.dumps(sweep_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (sweep_run_dir / "summary.md").write_text(
        _render_sweep_summary_md(sweep_payload), encoding="utf-8"
    )

    if json_output:
        print(json.dumps(sweep_payload, indent=2, ensure_ascii=False))
    else:
        print("Sweep summary")
        for line in _format_sweep_table(rows):
            print(line)
        print()
        if failed:
            print(f"{len(failed)} of {len(rows)} variants failed: {', '.join(failed)}")
        print(f"Sweep dir: {sweep_run_dir}")
        print("  summary: summary.md · summary.json · per-variant report.md + rendered.yaml")
    return 1 if failed else 0


__all__ = [
    "RunContext",
    "RunReport",
    "build",
    "build_agent_config",
    "run",
    "run_from_config",
    "run_spec_once",
    "run_sweep_from_config",
]
