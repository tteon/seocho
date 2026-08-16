from __future__ import annotations

import json
import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional, Sequence

from .curation_design import load_curation_design_spec
from .graph_projector import GraphProjector
from .models import Memory
from .observability import StageTimer
from .qualification import (
    CurationDecisionResult,
    CurationPreview,
    GraphProjectionResult,
    QualificationCase,
    QualificationRunResult,
)
from .qualification_store import QualificationStore
from .query.answering import QueryAnswerSynthesizer
from .query.contracts import QueryPlan
from .query.executor import GraphQueryExecutor
from .query.planner import DeterministicQueryPlanner
from .query.run_metadata import build_local_query_metadata
from .runtime_contract import DEFAULT_QUERY_MODE, normalize_query_mode
from .store.llm import complete_with_task_hints

logger = logging.getLogger(__name__)
_FOUR_DIGIT_YEAR_RE = re.compile(r"\b(20\d{2})\b")

# The in-flight per-request run context, isolated per execution context so that
# concurrent multi-tenant asks() never observe each other's workspace/pin (the
# structured-runtime B7 fix). Mid-run consumers (the structured orchestrator, the
# synthesizer, tracing) read THIS, never a shared instance attribute.
_ACTIVE_RUN_CONTEXT: "ContextVar[Any]" = ContextVar("seocho_active_run_context", default=None)


def active_run_context() -> Any:
    """The `OntologyRunContext` of the request running in the current execution
    context, or None. Concurrency-safe (unlike the post-hoc
    `_LocalEngine.last_run_context()`)."""
    return _ACTIVE_RUN_CONTEXT.get()


class _LocalEngine:
    """Internal orchestrator for local engine mode.

    Wires together Ontology -> IndexingPipeline -> QueryStrategy -> GraphStore.
    """

    def __init__(
        self,
        *,
        ontology: Any,  # Ontology
        graph_store: Any,  # GraphStore
        llm: Any,  # LLMBackend
        vector_store: Any = None,
        workspace_id: str,
        extraction_prompt: Optional[Any] = None,  # PromptTemplate
        agent_config: Optional[Any] = None,  # AgentConfig
        ontology_profile: str = "default",
        qualification_store_path: Optional[str] = None,
        qualification_store_backend: str = "sqlite",
        curation_design: Optional[Any] = None,
    ) -> None:
        from .agent_config import AgentConfig
        from .events import NullEventPublisher
        from .index.ingestion_facade import IngestRequest, IngestionFacade
        from .indexing import IndexingPipeline
        from .ontology import Ontology
        from .prompt_strategy import ExtractionStrategy, LinkingStrategy, QueryStrategy

        self.ontology: Ontology = ontology
        self.graph_store = graph_store
        self.llm = llm
        self._vector_store = vector_store
        self.workspace_id = workspace_id
        self.agent_config: AgentConfig = agent_config or AgentConfig()
        self.extraction_prompt = extraction_prompt
        self.ontology_profile = str(ontology_profile or "default")
        self.qualification_store_path = str(qualification_store_path or "").strip() or None
        self.qualification_store_backend = str(qualification_store_backend or "sqlite")
        self._curation_design = load_curation_design_spec(curation_design)
        self._qualification_store: Optional[QualificationStore] = None

        from .ontology_context import OntologyContextCache

        self._ontology_context_cache = OntologyContextCache()
        self._last_query_metadata: Dict[str, Any] = {}
        # Per-request run-context spine (seocho structured-runtime, ADR-0200):
        # the typed OntologyRunContext built once per ask() and exposed via
        # last_run_context(). Optional RCU wiring (a VersionPinRegistry + active
        # pointer) makes the request pin ONE frozen ontology version for its whole
        # duration; all default to None so behaviour is unchanged until configured.
        self._ontology_package_id = str(
            getattr(ontology, "package_id", "") or getattr(ontology, "name", "") or "default"
        )
        self._ontology_pin_registry: Any = None
        self._active_ontology_pointer: Any = None
        self._last_run_context: Any = None
        # Structured engine (ADR-0205): the organ-flagged orchestrator path. All
        # optional/injectable so `engine="deterministic"` (the default) never
        # touches any of this. A snapshot-store-backed resolver enables the pinned
        # schema organ; the two seams default to real (LLM text2cypher +
        # QueryAnswerSynthesizer) but are injectable for tests.
        from .query.arm_config import ArmConfig

        self._structured_arm = ArmConfig.governed()
        self._pinned_schema_resolver: Any = None
        self._structured_cypher_generator: Any = None
        self._structured_synthesizer: Any = None
        self._events = NullEventPublisher()
        self._ingest_request_cls = IngestRequest

        # Resolve embedding backend from the LLM if the provider supports it.
        embedding_backend = None
        if hasattr(llm, "embed") and getattr(getattr(llm, "provider_spec", None), "supports_embeddings", False):
            embedding_backend = llm

        # Indexing pipeline (handles chunking, extraction, validation, dedup, write).
        self._indexing = IndexingPipeline(
            ontology=ontology,
            graph_store=graph_store,
            llm=llm,
            vector_store=vector_store,
            workspace_id=workspace_id,
            extraction_prompt=extraction_prompt,
            enable_rule_constraints=True,
            embedding_backend=embedding_backend,
            ontology_profile=self.ontology_profile,
            ontology_context_cache=self._ontology_context_cache,
            enforcement=getattr(self.agent_config, "ontology_enforcement", "guided"),
        )
        self._indexing._quality_threshold = self.agent_config.extraction_quality_threshold
        self._indexing._max_retries = self.agent_config.extraction_max_retries
        self._ingestion = IngestionFacade(self._indexing, publisher=self._events)

        # Pre-build strategies (for extract-only and query).
        self._extraction = ExtractionStrategy(ontology, extraction_prompt=extraction_prompt)
        self._linking = LinkingStrategy(ontology)
        self._query = QueryStrategy(ontology)

    def _resolve_qualification_store(
        self,
        *,
        path: Optional[str] = None,
        backend: Optional[str] = None,
    ) -> QualificationStore:
        resolved_path = str(path or self.qualification_store_path or "").strip()
        if not resolved_path:
            raise RuntimeError(
                "qualification store is not configured. Set qualification_store_path "
                "on Seocho(...) or pass one to qualify_graph()."
            )
        resolved_backend = str(backend or self.qualification_store_backend or "sqlite")
        if (
            self._qualification_store is None
            or self._qualification_store.path != resolved_path
            or self._qualification_store.backend != resolved_backend
        ):
            if self._qualification_store is not None:
                self._qualification_store.close()
            self._qualification_store = QualificationStore(
                resolved_path,
                backend=resolved_backend,
            )
        return self._qualification_store

    def _build_memory_from_indexing_result(
        self,
        result: Any,
        *,
        content: str,
        database: str,
        category: str,
        metadata: Optional[Dict[str, Any]],
        source_type: str,
    ) -> Memory:
        result_metadata: Dict[str, Any] = {
            "category": category,
            "nodes_created": result.total_nodes,
            "relationships_created": result.total_relationships,
            "chunks_processed": result.chunks_processed,
            "validation_errors": result.validation_errors,
            "write_errors": result.write_errors,
            "skipped_chunks": result.skipped_chunks,
            "deduplicated": result.deduplicated,
            **(metadata or {}),
        }
        if result.rule_profile is not None:
            result_metadata["rule_profile"] = result.rule_profile
        if result.rule_validation_summary is not None:
            result_metadata["rule_validation_summary"] = result.rule_validation_summary
        if result.semantic_artifacts is not None:
            result_metadata["semantic_artifacts"] = result.semantic_artifacts
        if result.ontology_context is not None:
            result_metadata["ontology_context"] = result.ontology_context
            result_metadata["ontology_context_hash"] = result.ontology_context.get("context_hash", "")
            result_metadata["ontology_profile"] = result.ontology_context.get("profile", self.ontology_profile)
        if result.layered_graph_summary is not None:
            result_metadata["layered_graph_summary"] = result.layered_graph_summary
        if result.fallback_used:
            result_metadata["fallback_used"] = True
            result_metadata["fallback_reason"] = result.fallback_reason
        if self.qualification_store_path:
            try:
                capture = self._resolve_qualification_store().record_indexing_result(
                    result=result,
                    workspace_id=self.workspace_id,
                    graph_id=database,
                    database=database,
                    content=content,
                    metadata=metadata,
                )
                result_metadata["qualification_capture"] = capture
            except Exception as exc:
                logger.warning("Qualification artifact capture skipped: %s", exc)
                result_metadata["qualification_capture_error"] = str(exc)
        try:
            from .indexing_design import build_reasoning_cycle_report

            reasoning_cycle = build_reasoning_cycle_report(
                result_metadata,
                validation_errors=result.validation_errors,
                write_errors=result.write_errors,
                fallback_used=result.fallback_used,
                fallback_reason=result.fallback_reason,
            )
            if reasoning_cycle is not None:
                result_metadata["reasoning_cycle"] = reasoning_cycle
        except Exception:
            pass

        content_preview = content[:500]
        if not content_preview:
            content_preview = ", ".join(
                str(node.get("properties", {}).get("name") or node.get("id") or "").strip()
                for node in list(getattr(result, "nodes", []) or [])[:6]
                if str(node.get("properties", {}).get("name") or node.get("id") or "").strip()
            )[:500]

        return Memory(
            memory_id=result.source_id,
            workspace_id=self.workspace_id,
            content=content_preview,
            metadata=result_metadata,
            status="active" if result.ok else "failed",
            database=database,
            category=category,
            source_type=source_type,
            entities=list(getattr(result, "nodes", []) or []),
        )

    def close(self) -> None:
        if self._qualification_store is not None:
            self._qualification_store.close()
            self._qualification_store = None

    def qualify_graph(
        self,
        *,
        database: str,
        graph_id: Optional[str] = None,
        curation_design: Optional[Any] = None,
        modes: Sequence[str] = ("text", "graph", "llm"),
        scope: Optional[Dict[str, Any]] = None,
        qualification_store_path: Optional[str] = None,
        qualification_store_backend: Optional[str] = None,
    ) -> QualificationRunResult:
        store = self._resolve_qualification_store(
            path=qualification_store_path,
            backend=qualification_store_backend,
        )
        design = load_curation_design_spec(curation_design or self._curation_design)
        return store.qualify_graph(
            workspace_id=self.workspace_id,
            graph_id=str(graph_id or database),
            database=database,
            ontology=self.ontology,
            curation_design=design,
            llm=self.llm,
            modes=modes,
            scope=scope,
        )

    def list_curation_cases(
        self,
        *,
        run_id: Optional[str] = None,
        status: Optional[str] = None,
        case_type: Optional[str] = None,
        limit: int = 100,
        qualification_store_path: Optional[str] = None,
        qualification_store_backend: Optional[str] = None,
    ) -> List[QualificationCase]:
        store = self._resolve_qualification_store(
            path=qualification_store_path,
            backend=qualification_store_backend,
        )
        return store.list_cases(
            run_id=run_id,
            status=status,
            case_type=case_type,
            limit=limit,
        )

    def preview_curation_decision(
        self,
        case_id: str,
        *,
        action: str,
        chosen_canonical_id: Optional[str] = None,
        property_resolution: Optional[Dict[str, Any]] = None,
        qualification_store_path: Optional[str] = None,
        qualification_store_backend: Optional[str] = None,
    ) -> CurationPreview:
        store = self._resolve_qualification_store(
            path=qualification_store_path,
            backend=qualification_store_backend,
        )
        return store.preview_decision(
            case_id,
            action=action,
            chosen_canonical_id=chosen_canonical_id,
            property_resolution=property_resolution,
        )

    def apply_curation_decision(
        self,
        case_id: str,
        *,
        action: str,
        actor_id: str = "local-user",
        actor_type: str = "user",
        chosen_canonical_id: Optional[str] = None,
        property_resolution: Optional[Dict[str, Any]] = None,
        qualification_store_path: Optional[str] = None,
        qualification_store_backend: Optional[str] = None,
    ) -> CurationDecisionResult:
        store = self._resolve_qualification_store(
            path=qualification_store_path,
            backend=qualification_store_backend,
        )
        return store.apply_decision(
            case_id,
            action=action,
            actor_id=actor_id,
            actor_type=actor_type,
            chosen_canonical_id=chosen_canonical_id,
            property_resolution=property_resolution,
        )

    def project_canonical_graph(
        self,
        *,
        database: str,
        graph_id: Optional[str] = None,
        run_id: Optional[str] = None,
        qualification_store_path: Optional[str] = None,
        qualification_store_backend: Optional[str] = None,
    ) -> GraphProjectionResult:
        store = self._resolve_qualification_store(
            path=qualification_store_path,
            backend=qualification_store_backend,
        )
        snapshot = store.build_projection_snapshot(
            workspace_id=self.workspace_id,
            graph_id=str(graph_id or database),
            database=database,
            run_id=run_id,
        )
        projector = GraphProjector(
            graph_store=self.graph_store,
            workspace_id=self.workspace_id,
        )
        # Stamp the active ontology version onto projected data so drift
        # detection is not blind on this path (seocho-ia4.1).
        ontology_context = None
        if getattr(self, "ontology", None) is not None:
            try:
                ontology_context = self._ontology_context_cache.get(
                    self.ontology,
                    workspace_id=self.workspace_id,
                    profile=self.ontology_profile,
                )
            except Exception:
                ontology_context = None
        return projector.project(
            snapshot, database=database, ontology_context=ontology_context
        )

    def add(
        self,
        content: str,
        *,
        database: str = "neo4j",
        category: str = "memory",
        metadata: Optional[Dict[str, Any]] = None,
        strict_validation: bool = False,
        ontology_override: Optional[Any] = None,
    ) -> Memory:
        """Chunk -> Extract -> Validate -> Link -> Write pipeline."""
        if ontology_override is not None:
            from .index.ingestion_facade import IngestionFacade
            from .indexing import IndexingPipeline

            pipeline = IndexingPipeline(
                ontology=ontology_override,
                graph_store=self.graph_store,
                llm=self.llm,
                vector_store=self._vector_store,
                workspace_id=self.workspace_id,
                extraction_prompt=self.extraction_prompt,
                strict_validation=strict_validation,
                enable_rule_constraints=True,
                ontology_profile=self.ontology_profile,
                ontology_context_cache=self._ontology_context_cache,
                enforcement=getattr(self.agent_config, "ontology_enforcement", "guided"),
            )
            ingestion = IngestionFacade(pipeline, publisher=self._events)
        else:
            ingestion = self._ingestion

        result = ingestion.ingest(
            self._ingest_request_cls(
                content=content,
                workspace_id=self.workspace_id,
                database=database,
                category=category,
                metadata=metadata,
                strict_validation=strict_validation,
            )
        )
        return self._build_memory_from_indexing_result(
            result,
            content=content,
            database=database,
            category=category,
            metadata=metadata,
            source_type="text",
        )

    def add_graph(
        self,
        graph_data: Dict[str, Any],
        *,
        content: str = "",
        database: str = "neo4j",
        category: str = "memory",
        metadata: Optional[Dict[str, Any]] = None,
        strict_validation: bool = False,
        chunk_records: Optional[Sequence[Dict[str, Any]]] = None,
        ontology_override: Optional[Any] = None,
    ) -> Memory:
        """Validate and write a caller-supplied graph payload."""
        if ontology_override is not None:
            from .indexing import IndexingPipeline

            pipeline = IndexingPipeline(
                ontology=ontology_override,
                graph_store=self.graph_store,
                llm=self.llm,
                vector_store=self._vector_store,
                workspace_id=self.workspace_id,
                extraction_prompt=self.extraction_prompt,
                strict_validation=strict_validation,
                enable_rule_constraints=True,
                ontology_profile=self.ontology_profile,
                ontology_context_cache=self._ontology_context_cache,
                enforcement=getattr(self.agent_config, "ontology_enforcement", "guided"),
            )
        else:
            pipeline = self._indexing

        original_strict = pipeline.strict_validation
        policy = getattr(pipeline, "enforcement_policy", None)
        policy_floor = bool(policy is not None and policy.violation_action == "reject")
        pipeline.strict_validation = bool(strict_validation) or policy_floor
        try:
            result = pipeline.index_graph(
                graph_data,
                content=content,
                database=database,
                category=category,
                metadata=metadata,
                chunk_records=chunk_records,
            )
        finally:
            pipeline.strict_validation = original_strict

        source_type = "structured_graph"
        if isinstance(metadata, dict):
            source_type = str(metadata.get("source_type") or source_type)
        return self._build_memory_from_indexing_result(
            result,
            content=content,
            database=database,
            category=category,
            metadata=metadata,
            source_type=source_type,
        )

    def add_batch(
        self,
        documents: Sequence[str],
        *,
        database: str = "neo4j",
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
        strict_validation: bool = False,
        on_progress: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Index multiple documents with progress tracking."""
        self._indexing.strict_validation = strict_validation
        batch_result = self._indexing.index_batch(
            documents,
            database=database,
            category=category,
            metadata=metadata,
            on_document=on_progress,
        )
        return batch_result.to_dict()

    def extract(
        self,
        content: str,
        *,
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run extraction only (no graph write)."""
        system, user = self._extraction.render(content, metadata=metadata, category=category)

        response = complete_with_task_hints(
            self.llm,
            system=system,
            user=user,
            temperature=0.0,
            response_format={"type": "json_object"},
            reasoning_mode=False,
            task_hint="json_extraction",
        )

        try:
            result = response.json()
        except (json.JSONDecodeError, ValueError):
            logger.error("LLM returned non-JSON extraction response: %s", response.text[:200])
            result = {"nodes": [], "relationships": [], "_extraction_failed": True}

        if not result.get("nodes") and not result.get("relationships"):
            logger.warning("Extraction produced no entities or relationships from input text")

        return result

    def ask(
        self,
        question: str,
        *,
        database: str = "neo4j",
        reasoning_mode: Optional[bool] = None,
        repair_budget: Optional[int] = None,
        query_mode: str = DEFAULT_QUERY_MODE,
        ontology_override: Optional[Any] = None,
        engine: str = "deterministic",
    ) -> str:
        """Ontology-aware query: generate Cypher -> execute -> synthesize answer.

        ``engine`` selects the runtime (orthogonal to ``query_mode``, which is
        reasoning semantics): ``"deterministic"`` (default) is the existing
        monolithic pipeline; ``"structured"`` routes through the organ-flagged
        :class:`~seocho.query.structured_orchestrator.StructuredQueryOrchestrator`
        (the governed multi-agent path the arm×organ e2e exercises)."""
        query_mode = normalize_query_mode(query_mode)
        active_ontology = ontology_override or self.ontology
        ontology_context = self._ontology_context_cache.get(
            active_ontology,
            workspace_id=self.workspace_id,
            profile=self.ontology_profile,
        )
        if ontology_override is not None:
            from .prompt_strategy import QueryStrategy

            self._query = QueryStrategy(active_ontology)

        if reasoning_mode is None:
            reasoning_mode = self.agent_config.reasoning_mode
        if repair_budget is None:
            repair_budget = self.agent_config.repair_budget
        # RouteProfile (trace-derived, A/B-gated): when SEOCHO_ROUTE_PROFILE is
        # set, classify the question's route_class and let its planner choose
        # the execution levers — escalate to multi-step (reasoning + repair)
        # ONLY for multi-hop, keep simple lookups on the cheap single pass
        # (exp5: planner only beats single_call on multi-hop). Default (env
        # unset) preserves the caller/agent_config values exactly.
        self._last_route_profile = None
        if os.getenv("SEOCHO_ROUTE_PROFILE") and query_mode != "graph_cot":
            from .query.route_profile import planner_exec_params, select_route_profile

            profile = select_route_profile(question)
            params = planner_exec_params(profile.planner)
            reasoning_mode = params["reasoning_mode"]
            repair_budget = params["repair_budget"]
            self._last_route_profile = profile
        if query_mode == "graph_cot":
            reasoning_mode = True
            repair_budget = max(1, int(repair_budget or 0))

        # ADR-0103 S4/H3: semantic-layer lane (decompose -> arbitrate -> compile)
        # with the operational route policy. Additive + env-gated
        # (SEOCHO_SEMANTIC_LAYER): STRUCTURED returns the exact-key answer;
        # CLARIFY surfaces a clarification (offer available periods) rather than a
        # silent empty result; NARRATIVE/FAIL fall through to the existing lane
        # (which may chunk-fallback), preserving current behavior. The arbiter
        # route is emitted as a tracing span for observability.
        self._last_semantic_route = None
        self._last_semantic_hint = None
        if (os.environ.get("SEOCHO_SEMANTIC_LAYER", "").strip().lower()
                in ("1", "true", "yes") and query_mode != "graph_cot"):
            try:
                from .query.semantic_query import clarification_message, semantic_answer

                sr = semantic_answer(
                    question, llm=self.llm, graph_store=self.graph_store,
                    database=database, workspace_id=self.workspace_id,
                )
                self._last_semantic_route = sr.route
                self._last_semantic_hint = sr.hint
                self._log_semantic_route(question, sr)
                if sr.answer is not None:                       # STRUCTURED hit
                    return sr.answer
                if sr.route == "CLARIFY":                       # offer a clarification
                    return clarification_message(sr.hint)
                # NARRATIVE / FAIL: fall through to the existing lane below
            except Exception as exc:  # never let the new lane break ask()
                logger.warning("Semantic-layer lane skipped: %s", exc)

        # ADR-0144: wrap the retrieval pipeline in a single rag.ask root span so
        # its stages (compile_cypher -> execute -> retrieve_ctx -> synthesize)
        # nest as a tree in Tempo instead of one flat sdk.query event.
        from .tracing import start_span

        # Build the per-request run context ONCE (workspace-scoped, typed) and,
        # when an RCU pin registry is configured, pin ONE frozen ontology version
        # for the whole request; the deterministic pipeline runs inside that pin.
        from contextlib import nullcontext

        from .ontology.run_context import build_local_ontology_run_context, pinned_run_context

        run_context = build_local_ontology_run_context(
            ontology_context,
            workspace_id=self.workspace_id,
            database=database,
            reasoning_mode=bool(reasoning_mode),
            repair_budget=int(repair_budget or 0),
        )
        pin_cm = (
            pinned_run_context(
                run_context,
                pin_registry=self._ontology_pin_registry,
                package_id=self._ontology_package_id,
                active_pointer=self._active_ontology_pointer,
            )
            if self._ontology_pin_registry is not None
            else nullcontext(run_context)
        )
        with start_span(
            "rag.ask",
            input_data={"question": question[:200]},
            metadata={
                "workspace_id": self.workspace_id,
                "query_mode": query_mode,
                "ontology": getattr(active_ontology, "name", ""),
            },
            tags=["rag"],
        ), pin_cm as pinned_context:
            # Publish the in-flight context in a ContextVar (isolated per execution
            # context) — NEVER on a shared instance attribute, so concurrent
            # multi-tenant asks() cannot clobber each other (B7).
            token = _ACTIVE_RUN_CONTEXT.set(pinned_context)
            try:
                if str(engine) == "structured":
                    return self._run_structured_pipeline(
                        question,
                        database=database,
                        active_ontology=active_ontology,
                        run_context=pinned_context,
                    )
                return self._run_query_pipeline(
                    question,
                    database=database,
                    reasoning_mode=reasoning_mode,
                    repair_budget=repair_budget,
                    query_mode=query_mode,
                    active_ontology=active_ontology,
                    ontology_context=ontology_context,
                )
            finally:
                # Fold the drift outcome the pipeline computed into the LOCAL
                # request context (not a shared attr), then publish it as the
                # last request's context (post-hoc convenience only).
                ctx = pinned_context
                mismatch = (self._last_query_metadata or {}).get("ontology_context_mismatch")
                if mismatch:
                    ctx = ctx.with_mismatch(mismatch)
                _ACTIVE_RUN_CONTEXT.reset(token)
                self._last_run_context = ctx

    def last_run_context(self) -> Any:
        """The typed :class:`OntologyRunContext` of the MOST RECENT ``ask`` on this
        engine — workspace-scoped, carrying the ontology identity/hash, the
        RCU-pinned version, and the drift outcome.

        This is a **post-hoc convenience and is NOT concurrency-safe**: under
        concurrent multi-tenant calls it reflects whichever request finished last.
        Mid-run consumers must read :func:`active_run_context` (ContextVar-isolated)
        instead."""
        return self._last_run_context

    def _structured_seams(self, active_ontology: Any, database: str):
        """The (cypher_generator, synthesizer) the structured orchestrator drives.
        Injectable (set the ``_structured_*`` attrs) for tests; the defaults are the
        real LLM text2cypher + the QueryAnswerSynthesizer, invoked only with a live
        LLM/graph (the e2e)."""
        gen = self._structured_cypher_generator
        if gen is None:
            def gen(question: str, schema_text: str) -> str:  # noqa: E306
                cypher, _params, _intent, _err = self._generate_cypher(question, active_ontology)
                return cypher or ""
        synth = self._structured_synthesizer
        if synth is None:
            answerer = QueryAnswerSynthesizer(query_strategy=self._query, llm=self.llm)

            def synth(question: str, rows: List[Dict[str, Any]]) -> str:  # noqa: E306
                return answerer.synthesize(question, rows)
        return gen, synth

    def _run_structured_pipeline(
        self, question: str, *, database: str, active_ontology: Any, run_context: Any
    ) -> str:
        """Organ-flagged structured path (ADR-0205): resolve schema -> retrieve ->
        guardrail -> governed execute -> synthesize, over the per-request run
        context. A per-request GuardrailLedger keeps the before/after governance
        signal un-poisoned across tenants; abstain is honest (a guardrail
        rejection is reported as such, never as 'no supporting evidence')."""
        from .query.structured_orchestrator import StructuredQueryOrchestrator

        ledger = None
        try:
            from .integrations.openai_agents import GuardrailLedger
            ledger = GuardrailLedger()
        except Exception:
            ledger = None

        gen, synth = self._structured_seams(active_ontology, database)
        orchestrator = StructuredQueryOrchestrator(
            arm=self._structured_arm,
            graph_store=self.graph_store,
            ontology=active_ontology,
            cypher_generator=gen,
            synthesizer=synth,
            resolver=self._pinned_schema_resolver,
            get_schema_fn=lambda: self._get_schema_info(database),
            database=database,
        )
        result = orchestrator.answer(question, run_context, workspace_id=self.workspace_id)
        if ledger is not None:
            ledger.record(result.guardrail_violations)

        # Honest abstain (D5): a guardrail rejection is NOT "no evidence".
        if result.guardrail_rejected:
            answer_source = "structured_guardrail_rejected"
        elif not result.rows:
            answer_source = "structured_no_evidence"
        else:
            answer_source = "structured"

        self._last_query_metadata = {
            "schema_version": "local_query_metadata.v1",
            "workspace_id": self.workspace_id,
            "database": database,
            "engine": "structured",
            "answer_source": answer_source,
            "arm": self._structured_arm.to_dict(),
            "structured": result.to_dict(),
            "guardrail_ledger": ledger.summary() if ledger is not None else {},
            "semantic_context": {},
            "cypher": result.cypher,
            "result_count": len(result.rows),
        }
        return result.answer

    @contextmanager
    def _traced_stage(
        self,
        timer: StageTimer,
        timer_key: str,
        span_name: Optional[str] = None,
    ) -> Iterator[None]:
        """Run a StageTimer stage and emit a nested rag.* span (ADR-0144)."""
        from .tracing import start_span

        with timer.stage(timer_key):
            with start_span(
                span_name or f"rag.{timer_key}",
                metadata={"workspace_id": self.workspace_id},
                tags=["rag"],
            ):
                yield

    def _annotate_synthesis_span(
        self,
        span: Any,
        synthesizer: Any,
        ontology_context: Any,
    ) -> None:
        """Stamp gen_ai.* + prompt/cache identity on rag.synthesize (ADR-0144).

        External-API deployments control the prompt, not the model internals, so
        the joinable signal is (model, params, tokens) + the cacheable system-
        prompt prefix hash (stable_prefix_hash / ontology_context_hash).
        """
        from .tracing import is_tracing_enabled

        if not is_tracing_enabled():
            return
        try:
            attrs: Dict[str, Any] = {
                "gen_ai.request.model": getattr(self.llm, "model", "unknown"),
            }
            provider = getattr(self.llm, "provider", "") or getattr(
                self.llm, "provider_name", ""
            )
            if provider:
                attrs["gen_ai.system"] = str(provider)
            temp = getattr(synthesizer, "last_temperature", None)
            if temp is not None:
                attrs["gen_ai.request.temperature"] = temp
            usage = getattr(synthesizer, "last_usage", None) or {}
            if usage.get("prompt_tokens"):
                attrs["gen_ai.usage.input_tokens"] = int(usage["prompt_tokens"])
            if usage.get("completion_tokens"):
                attrs["gen_ai.usage.output_tokens"] = int(usage["completion_tokens"])
            if usage.get("total_tokens"):
                attrs["gen_ai.usage.total_tokens"] = int(usage["total_tokens"])
            try:
                layout = ontology_context.kv_cache_layout()
                if layout.get("stable_prefix_hash"):
                    attrs["stable_prefix_hash"] = layout["stable_prefix_hash"]
                if layout.get("context_hash"):
                    attrs["ontology_context_hash"] = layout["context_hash"]
            except Exception:
                pass
            span.set_metadata(attrs)
        except Exception:
            pass

    def _run_query_pipeline(
        self,
        question: str,
        *,
        database: str,
        reasoning_mode: bool,
        repair_budget: int,
        query_mode: str,
        active_ontology: Any,
        ontology_context: Any,
    ) -> str:
        """Retrieval pipeline body for ask(), wrapped by the rag.ask span."""
        timer = StageTimer()
        agent_design_pattern = str(self.agent_config.extra.get("agent_design_pattern", "") or "")
        if query_mode == "graph_cot" and not agent_design_pattern:
            agent_design_pattern = "graph_cot"

        with self._traced_stage(timer, "schema"):
            schema_info = self._get_schema_info(database)
        self._query.schema_info = schema_info
        planner = DeterministicQueryPlanner(
            ontology=active_ontology,
            llm=self.llm,
            workspace_id=self.workspace_id,
        )
        executor = GraphQueryExecutor(graph_store=self.graph_store, database=database)
        answer_synthesizer = QueryAnswerSynthesizer(
            query_strategy=self._query,
            llm=self.llm,
        )

        with self._traced_stage(timer, "plan", "rag.compile_cypher"):
            cypher, params, intent_data, error = self._generate_cypher(
                question,
                active_ontology,
                planner=planner,
            )
        if error:
            timer.mark_total()
            self._last_query_metadata = build_local_query_metadata(
                workspace_id=self.workspace_id,
                agent_design_pattern=agent_design_pattern,
                question=question,
                database=database,
                ontology=active_ontology,
                ontology_context=ontology_context,
                ontology_context_mismatch={},
                cypher=cypher,
                params=params,
                intent_data=intent_data,
                records=[],
                answer_text=error,
                attempts=[],
                repair_budget=repair_budget,
                query_mode=query_mode,
                latency_breakdown_ms=timer.to_dict(),
                vector_context="",
                error=error,
                answer_source="plan_error",
            )
            return error

        with self._traced_stage(timer, "execute", "rag.execute"):
            records, exec_error = self._execute_cypher(
                cypher,
                params,
                database,
                executor=executor,
            )
        # F8 multi-plan execution (ADR-0100): opt-in + route-scoped to
        # multi_hop. Build/execute the top-K candidate shapes and RRF-fuse
        # to lift recall on compositional questions. Single-plan path is
        # untouched everywhere else; fusion never loses the single result.
        self._last_multi_plan = None
        if intent_data and query_mode != "graph_cot":
            from .query.multi_plan import execute_multi_plan, multi_plan_enabled

            if multi_plan_enabled():
                from .query.route_profile import classify_route_class

                if classify_route_class(question) == "multi_hop":
                    from .query.cypher_builder import CypherBuilder

                    with timer.stage("multi_plan"):
                        mp = execute_multi_plan(
                            builder=CypherBuilder(active_ontology),
                            executor=executor,
                            question=question,
                            intent_data=intent_data,
                            workspace_id=self.workspace_id,
                        )
                    if mp.records:
                        records = mp.records
                        exec_error = None
                        self._last_multi_plan = mp
        if exec_error:
            timer.mark_total()
            self._last_query_metadata = build_local_query_metadata(
                workspace_id=self.workspace_id,
                agent_design_pattern=agent_design_pattern,
                question=question,
                database=database,
                ontology=active_ontology,
                ontology_context=ontology_context,
                ontology_context_mismatch={},
                cypher=cypher,
                params=params,
                intent_data=intent_data,
                records=records or [],
                answer_text=exec_error,
                attempts=[],
                repair_budget=repair_budget,
                query_mode=query_mode,
                latency_breakdown_ms=timer.to_dict(),
                vector_context="",
                error=exec_error,
                answer_source="execution_error",
            )
            return exec_error

        if not records and intent_data.get("intent") in ("relationship_lookup", "entity_lookup"):
            with timer.stage("neighbor_fallback"):
                from .query.cypher_builder import CypherBuilder

                fb_builder = CypherBuilder(active_ontology)
                fb_cypher, fb_params = fb_builder.build(
                    intent="neighbors",
                    anchor_entity=intent_data.get("anchor_entity", ""),
                    anchor_label=intent_data.get("anchor_label", ""),
                    workspace_id=self.workspace_id,
                )
                fb_records, _ = self._execute_cypher(fb_cypher, fb_params, database)
                if fb_records:
                    records = fb_records
                    cypher = fb_cypher
                    params = fb_params

        attempts = []
        # A plan hint enters the repair loop the same way an error does. Repair
        # previously fired only on `not records`, so a query that returned rows
        # while planning a full scan was a success that could never be improved
        # -- and that is exactly the query whose cost explodes with the graph:
        # ADR-0144 measured 25 db hits against 6.6M at SF1000 for the same
        # answer, while at SF1 the two shapes were 4 ms apart.
        plan_hint = self._plan_repair_hint(cypher, params, database,
                                           executor=executor, ontology=active_ontology)
        # Whether the original query already answered. A plan-hint-only repair
        # (this is True) is a COST optimisation, not a correctness one, so it
        # must never trade a correct answer for a cheaper wrong one — see the
        # result-count guard below.
        original_had_records = bool(records)
        if reasoning_mode and repair_budget > 0 and (not records or plan_hint):
            with timer.stage("repair"):
                attempts.append({"cypher": cypher, "result_count": len(records or []),
                                 "error": None, "plan_hint": plan_hint})

                for _attempt_num in range(repair_budget):
                    repair_cypher, repair_params, repair_error = self._generate_repair_query(
                        question,
                        attempts,
                        schema_info,
                        intent_data,
                        active_ontology,
                        planner=planner,
                    )
                    if repair_error or not repair_cypher:
                        break

                    repair_records, repair_exec_error = self._execute_cypher(
                        repair_cypher,
                        repair_params,
                        database,
                        executor=executor,
                    )
                    attempts.append(
                        {
                            "cypher": repair_cypher,
                            "result_count": len(repair_records) if repair_records else 0,
                            "error": repair_exec_error,
                        }
                    )

                    if repair_records:
                        # If the original already returned rows, the repair was
                        # fired for plan cost alone. Accepting a repair that
                        # returns FEWER rows would turn a correct answer into a
                        # cheaper, smaller one -- a silent correctness
                        # regression to save db-hits. Keep the original unless
                        # the repair at least matches it.
                        if original_had_records and \
                                len(repair_records) < len(records or []):
                            break
                        records = repair_records
                        cypher = repair_cypher
                        params = repair_params
                        break

        vector_context = ""
        if not records and hasattr(self, "_vector_store") and self._vector_store is not None:
            with timer.stage("vector"):
                try:
                    vs = self._vector_store
                    if hasattr(vs, "search"):
                        vresults = vs.search(question, limit=3)
                        if vresults:
                            vector_context = "\n".join(f"[Vector result] {r.text[:300]}" for r in vresults)
                except Exception:
                    pass
        # Graph-native chunk fallback (answerability fix): structured Cypher
        # returned nothing and no vector_store supplied context — retrieve the
        # graph's OWN Chunk text by question keywords so the chunk layer
        # contributes instead of leaving synthesis to model priors. The
        # answerability diagnosis (2026-06-03) showed the facts live in
        # Chunk.text while structured retrieval misses 70% of the time.
        # Opt-in (SEOCHO_CHUNK_FALLBACK) pending its A/B.
        if not records and not vector_context and self._chunk_fallback_enabled():
            with timer.stage("chunk_fallback"):
                chunk_ctx = self._graph_chunk_fallback(question, database)
                if chunk_ctx:
                    vector_context = chunk_ctx

        with self._traced_stage(timer, "ontology_context_check"):
            ontology_context_mismatch = self._query_ontology_context_mismatch(database, ontology_context)
            # Turn the detected mismatch into an enforced barrier instead of a
            # bare warning (seocho-ia4.1). policy='warn' keeps back-compat;
            # 'raise' throws OntologyDriftError; 'block' annotates blocked=True
            # so the caller can refuse to answer against a stale contract.
            from .ontology_context import enforce_drift_policy

            ontology_context_mismatch = enforce_drift_policy(
                ontology_context_mismatch,
                policy=self._drift_policy(),
                logger_obj=logger,
            )

            # Read-time repair (seocho-ia4.6): on a proceeding drift (mismatch but
            # not blocked), reconcile the retrieved records to the ACTIVE contract
            # before answering — drop soft-deleted (logically removed) rows and
            # strip deprecated properties. This makes the "repair" freshness
            # decision a real reconciliation instead of serving stale data as-is.
            # Cheap O(records) scan of self-describing data — no ontology reasoning
            # on the hot path.
            if (records and ontology_context_mismatch.get("mismatch")
                    and not ontology_context_mismatch.get("blocked")):
                from .ontology.freshness import repair_read

                records, _repair_report = repair_read(records)
                if _repair_report.dropped_records or _repair_report.stripped_property_keys:
                    ontology_context_mismatch["read_repair"] = _repair_report.to_dict()

        with self._traced_stage(timer, "deterministic_answer"):
            deterministic_answer = self._build_deterministic_answer(
                question,
                records,
                intent_data,
                answer_synthesizer=answer_synthesizer,
            )
        if deterministic_answer:
            timer.mark_total()
            self._last_query_metadata = build_local_query_metadata(
                workspace_id=self.workspace_id,
                agent_design_pattern=agent_design_pattern,
                question=question,
                database=database,
                ontology=active_ontology,
                ontology_context=ontology_context,
                ontology_context_mismatch=ontology_context_mismatch,
                cypher=cypher,
                params=params,
                intent_data=intent_data,
                records=records,
                answer_text=deterministic_answer,
                attempts=attempts,
                repair_budget=repair_budget,
                query_mode=query_mode,
                latency_breakdown_ms=timer.to_dict(),
                vector_context=vector_context,
                error="",
                answer_source="deterministic",
            )
            _query_elapsed = timer.to_dict().get("total_ms", 0.0) / 1000.0
            self._log_query_trace(
                question=question,
                ontology=active_ontology,
                cypher=cypher,
                result_count=len(records) if records else 0,
                reasoning_attempts=len(attempts) if reasoning_mode and attempts else 0,
                elapsed_seconds=_query_elapsed,
            )
            return deterministic_answer

        reasoning_trace = None
        if reasoning_mode and attempts:
            reasoning_trace = json.dumps(attempts, default=str)

        # ADR-0144: capture WHAT is fed to synthesis (the previously-dark
        # retrieved context). Bodies are content-gated; counts/intent always.
        from .tracing import capture_text, start_span

        _rec_preview = capture_text(json.dumps(records[:5], default=str)) if records else None
        with start_span(
            "rag.retrieve_ctx",
            output_data={
                "n_records": len(records) if records else 0,
                "has_vector_context": bool(vector_context),
                "intent": intent_data.get("intent") if intent_data else None,
            },
            metadata={
                "workspace_id": self.workspace_id,
                **({"records_preview": _rec_preview} if _rec_preview else {}),
            },
            tags=["rag"],
        ):
            pass

        with timer.stage("generation"):
            # AnswerShape (trace-derived): classify the question's expected
            # answer shape and steer the synthesizer toward a terse
            # value/name/location answer. DEFAULT ON (opt-out via
            # SEOCHO_ANSWER_SHAPE=0); explanation/unknown shapes emit no
            # directive, so prose answers are unchanged (CLAUDE.md §20).
            answer_shape = None
            from .query.answer_shape import answer_shape_enabled, classify_answer_shape

            if answer_shape_enabled():
                answer_shape = classify_answer_shape(question)
            with start_span(
                "rag.synthesize",
                output_data={"result_count": len(records) if records else 0},
                metadata={"workspace_id": self.workspace_id},
                tags=["rag"],
            ) as syn_span:
                answer_text = answer_synthesizer.synthesize(
                    question,
                    records,
                    reasoning_trace=reasoning_trace,
                    vector_context=vector_context,
                    answer_shape=answer_shape,
                )
                self._annotate_synthesis_span(
                    syn_span, answer_synthesizer, ontology_context
                )

        timer.mark_total()
        self._last_query_metadata = build_local_query_metadata(
            workspace_id=self.workspace_id,
            agent_design_pattern=agent_design_pattern,
            question=question,
            database=database,
            ontology=active_ontology,
            ontology_context=ontology_context,
            ontology_context_mismatch=ontology_context_mismatch,
            cypher=cypher,
            params=params,
            intent_data=intent_data,
            records=records,
            answer_text=answer_text,
            attempts=attempts,
            repair_budget=repair_budget,
            query_mode=query_mode,
            latency_breakdown_ms=timer.to_dict(),
            vector_context=vector_context,
            error="",
            answer_source="llm_synthesis",
        )
        _query_elapsed = timer.to_dict().get("total_ms", 0.0) / 1000.0
        self._log_query_trace(
            question=question,
            ontology=active_ontology,
            cypher=cypher,
            result_count=len(records) if records else 0,
            reasoning_attempts=len(attempts) if reasoning_mode and attempts else 0,
            elapsed_seconds=_query_elapsed,
        )

        return answer_text

    def _log_query_trace(
        self,
        *,
        question: str,
        ontology: Any,
        cypher: str,
        result_count: int,
        reasoning_attempts: int,
        elapsed_seconds: float,
    ) -> None:
        try:
            from .tracing import is_tracing_enabled, log_query

            if is_tracing_enabled():
                log_query(
                    question=question,
                    ontology_name=ontology.name,
                    ontology_package=getattr(ontology, "package_id", ontology.name),
                    model=getattr(self.llm, "model", "unknown"),
                    cypher=cypher,
                    result_count=result_count,
                    reasoning_attempts=reasoning_attempts,
                    elapsed_seconds=elapsed_seconds,
                    metadata=self._last_query_metadata,
                    workspace_id=self.workspace_id,
                    provider=getattr(self.llm, "provider", None),
                    stage="query",
                )
        except Exception:
            pass

    def _log_semantic_route(self, question: str, sr: Any) -> None:
        """Emit the arbiter route (ADR-0103 H3) as a tracing span for observability."""
        try:
            from .metrics import get_metrics
            from .tracing import is_tracing_enabled, log_span

            if not is_tracing_enabled():
                return
            hint = getattr(sr, "hint", None)
            log_span(
                "semantic.route",
                input_data={"question": question},
                output_data={"route": sr.route, "answer": sr.answer},
                metadata=(hint.to_span() if hint is not None else {"arbiter.route": sr.route}),
                tags=["semantic-layer", f"route:{sr.route}"],
            )
            get_metrics().add("seocho.arbiter.route.count", attributes={"route": sr.route})
        except Exception:
            pass

    def _drift_policy(self) -> str:
        """Ontology-drift enforcement policy: 'warn' (default, back-compat),
        'raise', or 'block'. Set via SEOCHO_ONTOLOGY_DRIFT_POLICY or the
        ``drift_policy`` attribute (seocho-ia4.1)."""
        import os

        pol = str(
            getattr(self, "drift_policy", None)
            or os.environ.get("SEOCHO_ONTOLOGY_DRIFT_POLICY", "warn")
        ).strip().lower()
        return pol if pol in {"warn", "raise", "block"} else "warn"

    def _query_ontology_context_mismatch(self, database: str, ontology_context: Any) -> Dict[str, Any]:
        from .ontology_context import query_ontology_context_mismatch

        return query_ontology_context_mismatch(
            self.graph_store,
            ontology_context,
            workspace_id=self.workspace_id,
            database=database,
        )

    def _get_schema_info(self, database: str) -> Dict[str, Any]:
        try:
            schema = self.graph_store.get_schema(database=database)
            return {
                "node_labels": ", ".join(schema.get("labels", [])),
                "relationship_types": ", ".join(schema.get("relationship_types", [])),
            }
        except Exception:
            return {}

    def _generate_cypher(
        self,
        question: str,
        ontology: Any,
        *,
        planner: Optional[DeterministicQueryPlanner] = None,
    ) -> tuple:
        active_planner = planner or DeterministicQueryPlanner(
            ontology=ontology,
            llm=self.llm,
            workspace_id=self.workspace_id,
        )
        plan = active_planner.plan(question)
        return plan.cypher, plan.params, plan.intent_data, plan.error

    def _execute_cypher(
        self,
        cypher: str,
        params: Dict,
        database: str,
        *,
        executor: Optional[GraphQueryExecutor] = None,
    ) -> tuple:
        active_executor = executor or GraphQueryExecutor(
            graph_store=self.graph_store,
            database=database,
        )
        execution = active_executor.execute(QueryPlan(question="", cypher=cypher, params=params))
        return execution.records, execution.error

    def _plan_repair_hint(self, cypher: str, params: Dict, database: str, *,
                          executor: Optional[GraphQueryExecutor] = None,
                          ontology: Optional[Any] = None) -> Optional[str]:
        """EXPLAIN the query and, if it plans a scan, say what to do instead.

        EXPLAIN rather than PROFILE: it compiles without executing, so this can
        gate every query instead of a sample, and the seek/scan distinction is
        already settled at plan time.

        Behind SEOCHO_PLAN_GATE and off by default, because it changes WHEN
        repair fires. A behaviour change belongs behind a flag until it has been
        measured on a real corpus.
        """
        import os

        if os.getenv("SEOCHO_PLAN_GATE", "").strip().lower() not in {"1", "true", "on"}:
            return None
        try:
            from .query.plan_quality import repair_hint, summarize_plan

            explained = (executor or GraphQueryExecutor(
                graph_store=self.graph_store, database=database,
            )).explain(QueryPlan(question="", cypher=cypher, params=params))
            return repair_hint(summarize_plan(explained), ontology)
        except Exception:  # noqa: BLE001 — a planning probe must never fail a query
            return None

    def _generate_repair_query(
        self,
        question: str,
        attempts: List[Dict],
        schema_info: Dict[str, Any],
        intent_data: Optional[Dict[str, Any]] = None,
        ontology: Optional[Any] = None,
        *,
        planner: Optional[DeterministicQueryPlanner] = None,
    ) -> tuple:
        active_planner = planner or DeterministicQueryPlanner(
            ontology=ontology or self.ontology,
            llm=self.llm,
            workspace_id=self.workspace_id,
        )
        plan = active_planner.repair(
            question=question,
            attempts=attempts,
            intent_data=intent_data,
            ontology=ontology,
        )
        return plan.cypher, plan.params, plan.error

    def _build_deterministic_answer(
        self,
        question: str,
        records: Sequence[Dict[str, Any]],
        intent_data: Optional[Dict[str, Any]],
        *,
        answer_synthesizer: Optional[QueryAnswerSynthesizer] = None,
    ) -> Optional[str]:
        active_answer_synthesizer = answer_synthesizer or QueryAnswerSynthesizer(
            query_strategy=self._query,
            llm=self.llm,
        )
        return active_answer_synthesizer.build_deterministic_answer(
            question,
            records,
            intent_data,
        )

    def _build_financial_answer(
        self,
        question: str,
        records: Sequence[Dict[str, Any]],
        intent_data: Dict[str, Any],
    ) -> Optional[str]:
        years = [str(year) for year in intent_data.get("years", []) if str(year).strip()]
        rows = self._normalize_financial_rows(records)
        if not rows:
            return None

        selected_rows = self._select_financial_rows(rows, intent_data)
        if not selected_rows:
            return None

        intent = str(intent_data.get("intent", ""))
        metric_label = self._humanize_metric_label(intent_data)
        company = selected_rows[0].get("company", "")

        if intent == "financial_metric_delta":
            target_years = self._ordered_years(years or [row["year"] for row in selected_rows])
            if len(target_years) < 2:
                return None
            start_year, end_year = target_years[0], target_years[-1]
            by_year = {row["year"]: row for row in selected_rows if row.get("year")}
            start_row = by_year.get(start_year)
            end_row = by_year.get(end_year)
            if not start_row or not end_row:
                available = ", ".join(sorted(by_year.keys()))
                return (
                    f"I found related {metric_label} evidence for {company or 'the company'}, "
                    f"but not enough period coverage to compare {start_year} and {end_year}. "
                    f"Available years: {available or 'none'}."
                )

            delta = round(end_row["value"] - start_row["value"], 3)
            direction = "increased" if delta > 0 else "decreased" if delta < 0 else "was flat"
            delta_abs = self._format_financial_number(abs(delta))
            start_value = self._format_financial_number(start_row["value"])
            end_value = self._format_financial_number(end_row["value"])
            if direction == "was flat":
                return (
                    f"For {company}, {metric_label} was flat from {start_year} to {end_year} "
                    f"at ${end_value}."
                )
            return (
                f"For {company}, {metric_label} {direction} by ${delta_abs} from {start_year} to {end_year}, "
                f"calculated as ${end_value} minus ${start_value}."
            )

        best_row = selected_rows[-1]
        year_suffix = f" in {best_row['year']}" if best_row.get("year") else ""
        return f"For {company}, {metric_label} was ${self._format_financial_number(best_row['value'])}{year_suffix}."

    def _normalize_financial_rows(self, records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, float, str]] = set()
        for record in records:
            if "metric_name" not in record or "value" not in record:
                continue
            value = self._coerce_number(record.get("value"))
            if value is None:
                continue
            year = self._coerce_year(record.get("year"), record.get("metric_name"), record.get("company"))
            company = str(record.get("company", "")).strip()
            metric_name = str(record.get("metric_name", "")).strip()
            key = (company, year, value, metric_name)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "company": company,
                    "metric_name": metric_name,
                    "year": year,
                    "value": value,
                    "relationship": str(record.get("relationship", "")),
                }
            )
        return rows

    def _select_financial_rows(
        self,
        rows: Sequence[Dict[str, Any]],
        intent_data: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        anchor = str(intent_data.get("anchor_entity", "")).strip()
        target_years = self._ordered_years(intent_data.get("years", []))
        metric_aliases = [str(alias).lower() for alias in intent_data.get("metric_aliases", [])]
        scope_tokens = [str(token).lower() for token in intent_data.get("metric_scope_tokens", [])]

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row.get("company", ""), []).append(row)

        best_company = ""
        best_score = -1
        for company, company_rows in grouped.items():
            years_present = {row.get("year", "") for row in company_rows if row.get("year")}
            metric_hits = 0
            for row in company_rows:
                text = str(row.get("metric_name", "")).lower()
                metric_hits += sum(1 for token in scope_tokens if token in text)
                metric_hits += sum(1 for alias in metric_aliases if alias in text)
            coverage = sum(1 for year in target_years if year in years_present)
            company_score = coverage * 10 + metric_hits + self._company_match_score(company, anchor)
            if company_score > best_score:
                best_score = company_score
                best_company = company

        selected = grouped.get(best_company, list(rows))
        if not target_years:
            return list(selected)

        best_by_year: Dict[str, Dict[str, Any]] = {}
        for row in selected:
            year = row.get("year", "")
            if not year:
                continue
            score = self._row_match_score(row, anchor, metric_aliases, scope_tokens)
            current = best_by_year.get(year)
            if current is None or score > self._row_match_score(current, anchor, metric_aliases, scope_tokens):
                best_by_year[year] = row
        return [best_by_year[year] for year in target_years if year in best_by_year]

    def _row_match_score(
        self,
        row: Dict[str, Any],
        anchor: str,
        metric_aliases: Sequence[str],
        scope_tokens: Sequence[str],
    ) -> int:
        score = self._company_match_score(str(row.get("company", "")), anchor)
        metric_text = str(row.get("metric_name", "")).lower()
        score += sum(3 for token in scope_tokens if token in metric_text)
        score += sum(1 for alias in metric_aliases if alias in metric_text)
        if str(row.get("relationship", "")) in {"REPORTED", "reported"}:
            score += 2
        return score

    def _company_match_score(self, company: str, anchor: str) -> int:
        if not anchor:
            return 0
        company_norm = re.sub(r"[^a-z0-9]+", " ", company.lower())
        anchor_norm = re.sub(r"[^a-z0-9]+", " ", anchor.lower())
        anchor_tokens = [token for token in anchor_norm.split() if token]
        return sum(2 for token in anchor_tokens if token in company_norm)

    def _coerce_number(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _chunk_fallback_enabled() -> bool:
        """Graph-native chunk fallback — DEFAULT OFF (opt-in via
        SEOCHO_CHUNK_FALLBACK) pending its answerability A/B."""
        return str(os.environ.get("SEOCHO_CHUNK_FALLBACK", "")).strip().lower() in ("1", "true", "yes")

    _CHUNK_KW_STOP = {
        "what", "was", "is", "are", "were", "the", "a", "an", "of", "in", "on",
        "for", "to", "and", "or", "how", "much", "many", "who", "which", "where",
        "did", "does", "do", "at", "by", "with", "during", "their", "its",
    }

    def _graph_chunk_fallback(self, question: str, database: str) -> str:
        """Retrieve the graph's own Chunk text by question keywords when
        structured retrieval is empty. cypher-safe: static :Chunk label,
        keyword list + workspace_id passed as parameters, read-only, LIMIT.
        Returns a joined context string or "" on no hit/error.
        """
        import re as _re

        toks = [
            t for t in _re.sub(r"[^a-z0-9 ]", " ", (question or "").lower()).split()
            if len(t) > 2 and t not in self._CHUNK_KW_STOP
        ]
        if not toks:
            return ""
        kws = toks[:8]
        try:
            rows = self.graph_store.query(
                "MATCH (c:Chunk) "
                "WHERE c._workspace_id = $workspace_id "
                "  AND any(kw IN $kws WHERE toLower(coalesce(c.text, '')) CONTAINS kw) "
                "RETURN c.text AS text "
                "LIMIT $k",
                params={"workspace_id": self.workspace_id, "kws": kws, "k": 3},
                database=database,
            )
        except Exception:
            return ""
        texts = [str(r.get("text", "")).strip() for r in (rows or []) if r.get("text")]
        if not texts:
            return ""
        return "\n".join(f"[Graph chunk] {t[:400]}" for t in texts)

    def _coerce_year(self, raw_year: Any, *fallback_fields: Any) -> str:
        text = str(raw_year).strip()
        if text and text.lower() != "none":
            if len(text) == 4 and text.isdigit():
                return text
        for field in fallback_fields:
            match = _FOUR_DIGIT_YEAR_RE.search(str(field))
            if match:
                return match.group(1)
        return ""

    def _ordered_years(self, years: Sequence[Any]) -> List[str]:
        deduped = []
        for year in years:
            text = str(year).strip()
            if text and text not in deduped:
                deduped.append(text)
        return sorted(deduped)

    def _humanize_metric_label(self, intent_data: Dict[str, Any]) -> str:
        metric_name = str(intent_data.get("metric_name", "")).strip()
        scope_tokens = [str(token) for token in intent_data.get("metric_scope_tokens", []) if str(token)]
        metric_aliases = [str(alias) for alias in intent_data.get("metric_aliases", []) if str(alias)]
        if metric_name:
            return metric_name.replace("&", "and")
        if scope_tokens and metric_aliases:
            return f"{' '.join(scope_tokens)} {metric_aliases[0]}".strip()
        if metric_aliases:
            return metric_aliases[0]
        return "financial metric"

    def _format_financial_number(self, value: float) -> str:
        return f"{value:,.1f}".rstrip("0").rstrip(".")

    def _link(
        self,
        nodes: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        *,
        category: str = "general",
    ) -> Dict[str, Any]:
        """Run entity linking/dedup."""
        self._linking.category = category
        entities_json = json.dumps({"nodes": nodes, "relationships": relationships}, default=str)
        system, user = self._linking.render(entities_json)

        response = complete_with_task_hints(
            self.llm,
            system=system,
            user=user,
            temperature=0.0,
            response_format={"type": "json_object"},
            reasoning_mode=False,
            task_hint="entity_linking",
        )

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return {"nodes": nodes, "relationships": relationships}
