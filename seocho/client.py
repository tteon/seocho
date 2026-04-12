"""
SEOCHO SDK client — ontology-first interface for knowledge graph
construction and querying.

Two modes of operation:

1. **Local engine mode** (ontology + graph_store + llm provided):
   All extraction, linking, and querying happens locally without a server.

2. **HTTP client mode** (base_url provided, default):
   Delegates to a running SEOCHO server.  Full backward compatibility.

Example — local mode::

    from seocho import Seocho, Ontology
    from seocho.graph_store import Neo4jGraphStore
    from seocho.llm_backend import OpenAIBackend

    onto = Ontology.from_yaml("schema.yaml")
    store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "pass")
    llm = OpenAIBackend(model="gpt-4o")

    s = Seocho(ontology=onto, graph_store=store, llm=llm)
    result = s.add("Samsung's CEO is Jay Y. Lee.", database="news_kg")
    answer = s.ask("Who is Samsung's CEO?")

Example — HTTP mode (unchanged from v0.1)::

    s = Seocho(base_url="http://localhost:8001")
    s.add("some text")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urljoin

import requests

from .exceptions import SeochoConnectionError, SeochoHTTPError
from .governance import ArtifactDiff, ArtifactValidationResult, diff_artifact_payloads, validate_artifact_payload
from .semantic import (
    ApprovedArtifacts,
    SemanticArtifact,
    SemanticArtifactDraftInput,
    SemanticArtifactSummary,
    SemanticPromptContext,
    serialize_optional_mapping,
)
from .models import (
    AgentRunResponse,
    ArchiveResult,
    ChatResponse,
    DebateRunResponse,
    EntityOverride,
    ExecutionPlan,
    ExecutionResult,
    FulltextIndexResponse,
    GraphRef,
    GraphTarget,
    Memory,
    MemoryCreateResult,
    PlatformChatResponse,
    PlatformSessionResponse,
    RawIngestResult,
    ReasoningPolicy,
    SearchResponse,
    SearchResult,
    SemanticRunRecord,
    SemanticRunResponse,
)

logger = logging.getLogger(__name__)


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class Seocho:
    """Ontology-first SDK for knowledge graph construction and querying.

    When ``ontology``, ``graph_store``, and ``llm`` are provided, the
    client operates in **local engine mode** — all extraction, linking,
    and querying happens in-process.

    When only ``base_url`` is provided (or defaulted), the client
    delegates to a running SEOCHO HTTP server (**HTTP client mode**).
    """

    def __init__(
        self,
        *,
        # --- Local engine mode ---
        ontology: Optional[Any] = None,  # seocho.ontology.Ontology
        graph_store: Optional[Any] = None,  # seocho.graph_store.GraphStore
        llm: Optional[Any] = None,  # seocho.llm_backend.LLMBackend
        vector_store: Optional[Any] = None,  # seocho.vector_store.VectorStore
        extraction_prompt: Optional[Any] = None,  # seocho.query.PromptTemplate
        agent_config: Optional[Any] = None,  # seocho.agent_config.AgentConfig
        # --- HTTP client mode ---
        base_url: Optional[str] = None,
        workspace_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.workspace_id = workspace_id or _env_str("SEOCHO_WORKSPACE_ID", "default")
        self.user_id = user_id or os.getenv("SEOCHO_USER_ID")
        self.agent_id = agent_id or os.getenv("SEOCHO_AGENT_ID")
        self.session_id = session_id or os.getenv("SEOCHO_SESSION_ID")
        self.timeout = timeout if timeout is not None else _env_float("SEOCHO_TIMEOUT", 30.0)

        # Local engine components
        self.ontology = ontology
        self.graph_store = graph_store
        self.llm = llm
        self.vector_store = vector_store
        self.extraction_prompt = extraction_prompt

        # Agent config
        if agent_config is None:
            from .agent_config import AgentConfig
            agent_config = AgentConfig()
        self.agent_config = agent_config

        # Determine mode
        self._local_mode = ontology is not None and graph_store is not None and llm is not None

        if self._local_mode:
            self._engine = _LocalEngine(
                ontology=ontology,
                graph_store=graph_store,
                llm=llm,
                workspace_id=self.workspace_id,
                extraction_prompt=extraction_prompt,
                agent_config=agent_config,
            )
            self._session = session or requests.Session()
            self.base_url = ""
        else:
            self._engine = None
            self.base_url = (base_url or _env_str("SEOCHO_BASE_URL", "http://localhost:8001")).rstrip("/") + "/"
            self._session = session or requests.Session()

        self._graph_catalog_cache: Optional[Dict[str, GraphTarget]] = None
        self._ontology_registry: Dict[str, Any] = {}  # database -> Ontology

    def register_ontology(self, database: str, ontology: Any) -> None:
        """Bind a specific ontology to a database.

        When ``add()`` or ``ask()`` targets this database, the
        registered ontology is used instead of the default.

        Parameters
        ----------
        database:
            Target database name.
        ontology:
            The :class:`~seocho.ontology.Ontology` for this database.
        """
        self._ontology_registry[database] = ontology

    def get_ontology(self, database: str) -> Any:
        """Get the ontology for a database (registered or default)."""
        return self._ontology_registry.get(database, self.ontology)

    # ------------------------------------------------------------------
    # Core API — works in both modes
    # ------------------------------------------------------------------

    def add(
        self,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        prompt_context: Optional[Dict[str, Any] | SemanticPromptContext] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        database: Optional[str] = None,
        category: str = "memory",
        source_type: str = "text",
        semantic_artifact_policy: str = "auto",
        approved_artifacts: Optional[Dict[str, Any] | ApprovedArtifacts] = None,
        approved_artifact_id: Optional[str] = None,
    ) -> Memory:
        """Add content to the knowledge graph.

        In local mode: extracts entities/relationships using the ontology-driven
        prompt strategy, then writes them to the graph store.

        In HTTP mode: sends to the SEOCHO server.
        """
        if self._local_mode:
            db = database or "neo4j"
            return self._engine.add(
                content,
                database=db,
                category=category,
                metadata=metadata,
                ontology_override=self._ontology_registry.get(db),
            )

        payload = self.add_with_details(
            content,
            metadata=metadata,
            prompt_context=prompt_context,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            database=database,
            category=category,
            source_type=source_type,
            semantic_artifact_policy=semantic_artifact_policy,
            approved_artifacts=approved_artifacts,
            approved_artifact_id=approved_artifact_id,
        )
        return payload.memory

    def ask(
        self,
        message: str,
        *,
        limit: int = 5,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
        databases: Optional[Sequence[str]] = None,
        database: Optional[str] = None,
        reasoning_mode: bool = False,
        repair_budget: int = 0,
    ) -> str:
        """Ask a natural-language question against the knowledge graph.

        In local mode: generates ontology-aware Cypher, executes it,
        and synthesizes an answer.  With ``reasoning_mode=True``,
        automatically retries with relaxed queries when results are
        empty (up to ``repair_budget`` attempts).

        In HTTP mode: delegates to the SEOCHO chat endpoint.
        """
        if self._local_mode:
            db = database or (databases[0] if databases and len(databases) > 0 else "neo4j")
            return self._engine.ask(
                message,
                database=db,
                reasoning_mode=reasoning_mode,
                repair_budget=repair_budget,
                ontology_override=self._ontology_registry.get(db),
            )

        return self.chat(
            message,
            limit=limit,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            graph_ids=graph_ids,
            databases=databases,
        ).assistant_message

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
        """Index multiple documents with chunking, validation, and dedup.

        Each document is automatically chunked if too long, extracted
        with ontology-aware prompts, validated against SHACL, and
        written to the graph. Duplicate content is detected and skipped.

        Parameters
        ----------
        documents:
            List of document texts.
        database:
            Target database.
        strict_validation:
            If True, chunks failing SHACL are rejected (not written).
        on_progress:
            Optional callback ``(doc_index, total_docs)``.

        Returns
        -------
        Summary dict with per-document results.

        Only available in local mode.
        """
        if not self._local_mode:
            raise RuntimeError("add_batch() requires local engine mode")
        return self._engine.add_batch(
            documents, database=database, category=category,
            metadata=metadata, strict_validation=strict_validation,
            on_progress=on_progress,
        )

    def index_file(
        self,
        path: str,
        *,
        database: str = "neo4j",
        category: str = "file",
        force: bool = False,
    ) -> Dict[str, Any]:
        """Index a single file (.txt, .md, .csv, .json, .jsonl).

        Reads the file, extracts entities using ontology-aware prompts,
        validates, and writes to the graph.  Tracks file changes so
        re-running on the same file is a no-op unless the file changed
        or ``force=True``.

        Parameters
        ----------
        path:
            Path to the file.
        force:
            Re-index even if the file hasn't changed.

        Returns
        -------
        Dict with ``path``, ``status``, ``records_found``, ``indexing``
        details.  Only available in local mode.
        """
        if not self._local_mode:
            raise RuntimeError("index_file() requires local engine mode")
        from .file_indexer import FileIndexer
        indexer = FileIndexer(self._engine._indexing, database=database, category=category)
        result = indexer.index_file(path, database=database, category=category, force=force)
        return result.to_dict()

    def index_directory(
        self,
        directory: str,
        *,
        database: str = "neo4j",
        category: str = "file",
        recursive: bool = True,
        force: bool = False,
        on_file: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Index all supported files in a directory.

        Scans for ``.txt``, ``.md``, ``.csv``, ``.json``, ``.jsonl``
        files and indexes each one.  Tracks which files have been
        indexed previously — unchanged files are skipped automatically.

        Parameters
        ----------
        directory:
            Path to the directory.
        recursive:
            Scan subdirectories.
        force:
            Re-index all files regardless of change status.
        on_file:
            Progress callback ``(file_path, current, total)``.

        Returns
        -------
        Dict with ``files_found``, ``files_indexed``, ``files_skipped``,
        ``files_failed``, ``files_unchanged``, and per-file results.
        Only available in local mode.
        """
        if not self._local_mode:
            raise RuntimeError("index_directory() requires local engine mode")
        from .file_indexer import FileIndexer
        indexer = FileIndexer(self._engine._indexing, database=database, category=category)
        result = indexer.index_directory(
            directory, database=database, category=category,
            recursive=recursive, force=force, on_file=on_file,
        )
        return result.to_dict()

    def reindex(
        self,
        source_id: str,
        content: str,
        *,
        database: str = "neo4j",
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Re-index a document: remove old graph data, then index fresh.

        Use this when the source content has changed and you want to
        update the knowledge graph without creating duplicates.

        Parameters
        ----------
        source_id:
            The source_id returned from the original ``add()`` call
            (available in ``Memory.memory_id``).
        content:
            The updated document text.

        Only available in local mode.
        """
        if not self._local_mode:
            raise RuntimeError("reindex() requires local engine mode")
        result = self._engine._indexing.reindex(
            source_id, content,
            database=database, category=category, metadata=metadata,
        )
        return result.to_dict()

    def delete_source(
        self,
        source_id: str,
        *,
        database: str = "neo4j",
    ) -> Dict[str, Any]:
        """Remove all graph data from a previously indexed source.

        Parameters
        ----------
        source_id:
            The source_id to remove (``Memory.memory_id``).

        Only available in local mode.
        """
        if not self._local_mode:
            raise RuntimeError("delete_source() requires local engine mode")
        return self._engine._indexing.delete_source(source_id, database=database)

    def extract(
        self,
        content: str,
        *,
        category: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Extract entities and relationships without writing to the graph.

        Only available in local mode.  Returns the raw extraction result
        (nodes + relationships).
        """
        if not self._local_mode:
            raise RuntimeError("extract() requires local engine mode (ontology + graph_store + llm)")
        return self._engine.extract(content, category=category, metadata=metadata)

    def query(
        self,
        cypher: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        database: str = "neo4j",
    ) -> List[Dict[str, Any]]:
        """Execute a raw Cypher query against the graph store.

        Only available in local mode.
        """
        if not self._local_mode:
            raise RuntimeError("query() requires local engine mode (ontology + graph_store + llm)")
        return self.graph_store.query(cypher, params=params, database=database)

    def ensure_constraints(self, *, database: str = "neo4j") -> Dict[str, Any]:
        """Apply ontology-derived constraints to the graph database.

        Only available in local mode.
        """
        if not self._local_mode:
            raise RuntimeError("ensure_constraints() requires local engine mode")
        return self.graph_store.ensure_constraints(self.ontology, database=database)

    def search_similar(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Find documents similar to query text using vector embeddings.

        Requires a ``vector_store`` to be provided at construction.

        Parameters
        ----------
        query:
            The text to search for similar documents.
        limit:
            Maximum number of results.

        Returns
        -------
        List of dicts with ``id``, ``text``, ``score``, ``metadata``.
        """
        if self.vector_store is None:
            raise RuntimeError(
                "search_similar() requires a vector_store. "
                "Provide one at construction: Seocho(vector_store=vs, ...)"
            )
        results = self.vector_store.search(query, limit=limit)
        return [
            {"id": r.id, "text": r.text, "score": r.score, "metadata": r.metadata}
            for r in results
        ]

    # ------------------------------------------------------------------
    # HTTP-mode methods (backward compatible)
    # ------------------------------------------------------------------

    def add_with_details(
        self,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        prompt_context: Optional[Dict[str, Any] | SemanticPromptContext] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        database: Optional[str] = None,
        category: str = "memory",
        source_type: str = "text",
        semantic_artifact_policy: str = "auto",
        approved_artifacts: Optional[Dict[str, Any] | ApprovedArtifacts] = None,
        approved_artifact_id: Optional[str] = None,
    ) -> MemoryCreateResult:
        resolved_metadata = dict(metadata or {})
        serialized_prompt_context = serialize_optional_mapping(
            prompt_context,
            field_name="prompt_context",
        )
        if serialized_prompt_context:
            resolved_metadata["semantic_prompt_context"] = serialized_prompt_context
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "content": content,
            "metadata": resolved_metadata,
            "category": category,
            "source_type": source_type,
            "semantic_artifact_policy": semantic_artifact_policy,
        }
        body.update(self._scope_payload(user_id=user_id, agent_id=agent_id, session_id=session_id))
        if database:
            body["database"] = database
        serialized_approved_artifacts = serialize_optional_mapping(
            approved_artifacts,
            field_name="approved_artifacts",
        )
        if serialized_approved_artifacts:
            body["approved_artifacts"] = serialized_approved_artifacts
        if approved_artifact_id:
            body["approved_artifact_id"] = approved_artifact_id
        payload = self._request_json("POST", "/api/memories", json_body=body)
        return MemoryCreateResult.from_dict(payload)

    def apply_artifact(
        self,
        artifact_id: str,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        prompt_context: Optional[Dict[str, Any] | SemanticPromptContext] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        database: Optional[str] = None,
        category: str = "memory",
        source_type: str = "text",
    ) -> MemoryCreateResult:
        return self.add_with_details(
            content,
            metadata=metadata,
            prompt_context=prompt_context,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            database=database,
            category=category,
            source_type=source_type,
            semantic_artifact_policy="approved_only",
            approved_artifact_id=artifact_id,
        )

    def get(self, memory_id: str, *, database: Optional[str] = None) -> Memory:
        params: Dict[str, Any] = {"workspace_id": self.workspace_id}
        if database:
            params["database"] = database
        payload = self._request_json("GET", f"/api/memories/{memory_id}", params=params)
        return Memory.from_dict(payload["memory"])

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
        databases: Optional[Sequence[str]] = None,
    ) -> List[SearchResult]:
        return self.search_with_context(
            query,
            limit=limit,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            graph_ids=graph_ids,
            databases=databases,
        ).results

    def search_with_context(
        self,
        query: str,
        *,
        limit: int = 5,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
        databases: Optional[Sequence[str]] = None,
    ) -> SearchResponse:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "query": query,
            "limit": limit,
        }
        body.update(self._scope_payload(user_id=user_id, agent_id=agent_id, session_id=session_id))
        if graph_ids:
            body["graph_ids"] = list(graph_ids)
        if databases:
            body["databases"] = list(databases)
        payload = self._request_json("POST", "/api/memories/search", json_body=body)
        return SearchResponse.from_dict(payload)

    def chat(
        self,
        message: str,
        *,
        limit: int = 5,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
        databases: Optional[Sequence[str]] = None,
    ) -> ChatResponse:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "message": message,
            "limit": limit,
        }
        body.update(self._scope_payload(user_id=user_id, agent_id=agent_id, session_id=session_id))
        if graph_ids:
            body["graph_ids"] = list(graph_ids)
        if databases:
            body["databases"] = list(databases)
        payload = self._request_json("POST", "/api/chat", json_body=body)
        return ChatResponse.from_dict(payload)

    def delete(self, memory_id: str, *, database: Optional[str] = None) -> ArchiveResult:
        params: Dict[str, Any] = {"workspace_id": self.workspace_id}
        if database:
            params["database"] = database
        payload = self._request_json("DELETE", f"/api/memories/{memory_id}", params=params)
        return ArchiveResult.from_dict(payload)

    def router(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
    ) -> AgentRunResponse:
        body = self._query_payload(query=query, user_id=user_id, graph_ids=graph_ids)
        payload = self._request_json("POST", "/run_agent", json_body=body)
        return AgentRunResponse.from_dict(payload)

    def react(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
    ) -> AgentRunResponse:
        """Run the graph-scoped tool-using router path."""
        return self.router(query, user_id=user_id, graph_ids=graph_ids)

    def advanced(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[GraphRef | GraphTarget | Dict[str, Any] | str]] = None,
    ) -> DebateRunResponse:
        """Run the explicit advanced multi-agent debate path."""
        return self.debate(query, user_id=user_id, graph_ids=graph_ids)

    def semantic(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[GraphRef | GraphTarget | Dict[str, Any] | str]] = None,
        databases: Optional[Sequence[str]] = None,
        entity_overrides: Optional[Sequence[EntityOverride | Dict[str, Any]]] = None,
        reasoning_mode: bool = False,
        repair_budget: int = 0,
    ) -> SemanticRunResponse:
        resolved_graph_ids: Optional[List[str]] = None
        resolved_databases = [str(item).strip() for item in databases or [] if str(item).strip()]
        if graph_ids:
            plain_graph_ids = [str(item).strip() for item in graph_ids if isinstance(item, str) and str(item).strip()]
            if len(plain_graph_ids) == len(graph_ids):
                resolved_graph_ids = plain_graph_ids
            else:
                inline_targets = [self._coerce_graph_ref(item) for item in graph_ids]
                resolved_targets = inline_targets if all(target.database for target in inline_targets) else self.resolve_graphs(*graph_ids)
                resolved_graph_ids = [target.graph_id for target in resolved_targets if target.graph_id]
                if not resolved_databases:
                    resolved_databases = [target.database for target in resolved_targets if target.database]
        body = self._query_payload(query=query, user_id=user_id, graph_ids=resolved_graph_ids)
        if resolved_databases:
            body["databases"] = resolved_databases
        if entity_overrides:
            body["entity_overrides"] = self._serialize_entity_overrides(entity_overrides)
        if reasoning_mode:
            body["reasoning_mode"] = True
        if repair_budget > 0:
            body["repair_budget"] = int(repair_budget)
        payload = self._request_json("POST", "/run_agent_semantic", json_body=body)
        return SemanticRunResponse.from_dict(payload)

    def debate(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[GraphRef | GraphTarget | Dict[str, Any] | str]] = None,
    ) -> DebateRunResponse:
        resolved_graph_ids: Optional[List[str]] = None
        if graph_ids:
            plain_graph_ids = [str(item).strip() for item in graph_ids if isinstance(item, str) and str(item).strip()]
            if len(plain_graph_ids) == len(graph_ids):
                resolved_graph_ids = plain_graph_ids
            else:
                inline_targets = [self._coerce_graph_ref(item) for item in graph_ids]
                resolved_targets = inline_targets if all(target.database for target in inline_targets) else self.resolve_graphs(*graph_ids)
                resolved_graph_ids = [target.graph_id for target in resolved_targets if target.graph_id]
        body = self._query_payload(query=query, user_id=user_id, graph_ids=resolved_graph_ids)
        payload = self._request_json("POST", "/run_debate", json_body=body)
        return DebateRunResponse.from_dict(payload)

    def plan(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> "ExecutionPlanBuilder":
        return ExecutionPlanBuilder(
            self,
            query,
            user_id=user_id if user_id is not None else self.user_id,
            session_id=session_id if session_id is not None else self.session_id,
        )

    def execute(self, plan: ExecutionPlan | Dict[str, Any]) -> ExecutionResult:
        if isinstance(plan, dict):
            plan = ExecutionPlan.from_dict(plan)
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an ExecutionPlan or compatible dict")

        resolved_plan = self._resolve_execution_plan(plan)
        style = resolved_plan.reasoning.normalized_style()

        if self._local_mode:
            return self._execute_local_plan(resolved_plan)

        if style == "debate":
            debate_result = self.advanced(
                resolved_plan.query,
                user_id=resolved_plan.user_id,
                graph_ids=resolved_plan.targets or None,
            )
            return ExecutionResult.from_run_result(
                requested_style="debate",
                runtime_mode="debate",
                resolved_targets=resolved_plan.targets,
                result=debate_result,
            )

        if style == "react":
            router_result = self.react(
                resolved_plan.query,
                user_id=resolved_plan.user_id,
                graph_ids=resolved_plan.graph_ids or None,
            )
            return ExecutionResult.from_run_result(
                requested_style="react",
                runtime_mode="router",
                resolved_targets=resolved_plan.targets,
                result=router_result,
            )

        semantic_result = self.semantic(
            resolved_plan.query,
            user_id=resolved_plan.user_id,
            graph_ids=resolved_plan.targets or None,
            databases=resolved_plan.databases or None,
            entity_overrides=resolved_plan.entity_overrides or None,
            reasoning_mode=resolved_plan.reasoning.repair_budget > 0,
            repair_budget=resolved_plan.reasoning.repair_budget,
        )
        return ExecutionResult.from_run_result(
            requested_style="direct",
            runtime_mode="semantic",
            resolved_targets=resolved_plan.targets,
            result=semantic_result,
        )

    def platform_chat(
        self,
        message: str,
        *,
        mode: str = "semantic",
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
        databases: Optional[Sequence[str]] = None,
        entity_overrides: Optional[Sequence[EntityOverride | Dict[str, Any]]] = None,
    ) -> PlatformChatResponse:
        body: Dict[str, Any] = {
            "message": message,
            "mode": mode,
            "workspace_id": self.workspace_id,
            "user_id": user_id if user_id is not None else self.user_id or "user_default",
        }
        if session_id is not None:
            body["session_id"] = session_id
        elif self.session_id:
            body["session_id"] = self.session_id
        if graph_ids:
            body["graph_ids"] = list(graph_ids)
        if databases:
            body["databases"] = list(databases)
        if entity_overrides:
            body["entity_overrides"] = self._serialize_entity_overrides(entity_overrides)
        payload = self._request_json("POST", "/platform/chat/send", json_body=body)
        return PlatformChatResponse.from_dict(payload)

    def session_history(self, session_id: str) -> PlatformSessionResponse:
        payload = self._request_json("GET", f"/platform/chat/session/{session_id}")
        return PlatformSessionResponse.from_dict(payload)

    def reset_session(self, session_id: str) -> PlatformSessionResponse:
        payload = self._request_json("DELETE", f"/platform/chat/session/{session_id}")
        return PlatformSessionResponse.from_dict(payload)

    def raw_ingest(
        self,
        records: Sequence[Dict[str, Any]],
        *,
        target_database: str,
        enable_rule_constraints: bool = True,
        create_database_if_missing: bool = True,
        semantic_artifact_policy: str = "auto",
        approved_artifacts: Optional[Dict[str, Any] | ApprovedArtifacts] = None,
        approved_artifact_id: Optional[str] = None,
    ) -> RawIngestResult:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "target_database": target_database,
            "records": [dict(item) for item in records],
            "enable_rule_constraints": enable_rule_constraints,
            "create_database_if_missing": create_database_if_missing,
            "semantic_artifact_policy": semantic_artifact_policy,
        }
        serialized_approved_artifacts = serialize_optional_mapping(
            approved_artifacts,
            field_name="approved_artifacts",
        )
        if serialized_approved_artifacts:
            body["approved_artifacts"] = serialized_approved_artifacts
        if approved_artifact_id:
            body["approved_artifact_id"] = approved_artifact_id
        payload = self._request_json("POST", "/platform/ingest/raw", json_body=body)
        return RawIngestResult.from_dict(payload)

    def graphs(self) -> List[GraphTarget]:
        payload = self._request_json("GET", "/graphs")
        graphs = [GraphTarget.from_dict(item) for item in payload.get("graphs", [])]
        self._graph_catalog_cache = {target.graph_id: target for target in graphs}
        return graphs

    def databases(self) -> List[str]:
        payload = self._request_json("GET", "/databases")
        return [str(item) for item in payload.get("databases", [])]

    def agents(self) -> List[str]:
        payload = self._request_json("GET", "/agents")
        return [str(item) for item in payload.get("agents", [])]

    def health(self, *, scope: str = "runtime") -> Dict[str, Any]:
        return self._request_json("GET", f"/health/{scope}")

    def semantic_runs(
        self,
        *,
        limit: int = 20,
        route: Optional[str] = None,
        intent_id: Optional[str] = None,
    ) -> List[SemanticRunRecord]:
        params: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "limit": max(1, int(limit or 20)),
        }
        if route:
            params["route"] = route
        if intent_id:
            params["intent_id"] = intent_id
        payload = self._request_json("GET", "/semantic/runs", params=params)
        return [SemanticRunRecord.from_dict(item) for item in payload.get("runs", [])]

    def semantic_run(self, run_id: str) -> SemanticRunRecord:
        params: Dict[str, Any] = {"workspace_id": self.workspace_id}
        payload = self._request_json("GET", f"/semantic/runs/{run_id}", params=params)
        return SemanticRunRecord.from_dict(payload)

    def ensure_fulltext_indexes(
        self,
        *,
        databases: Optional[Sequence[str]] = None,
        index_name: str = "entity_fulltext",
        labels: Optional[Sequence[str]] = None,
        properties: Optional[Sequence[str]] = None,
        create_if_missing: bool = True,
    ) -> FulltextIndexResponse:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "index_name": index_name,
            "create_if_missing": create_if_missing,
        }
        if databases:
            body["databases"] = list(databases)
        if labels:
            body["labels"] = list(labels)
        if properties:
            body["properties"] = list(properties)
        payload = self._request_json("POST", "/indexes/fulltext/ensure", json_body=body)
        return FulltextIndexResponse.from_dict(payload)

    def list_artifacts(self, *, status: Optional[str] = None) -> List[SemanticArtifactSummary]:
        params: Dict[str, Any] = {"workspace_id": self.workspace_id}
        if status:
            params["status"] = status
        payload = self._request_json("GET", "/semantic/artifacts", params=params)
        return [SemanticArtifactSummary.from_dict(item) for item in payload.get("artifacts", [])]

    def get_artifact(self, artifact_id: str) -> SemanticArtifact:
        params = {"workspace_id": self.workspace_id}
        payload = self._request_json("GET", f"/semantic/artifacts/{artifact_id}", params=params)
        return SemanticArtifact.from_dict(payload)

    def create_artifact_draft(
        self,
        draft: SemanticArtifactDraftInput | Dict[str, Any],
    ) -> SemanticArtifact:
        payload = serialize_optional_mapping(draft, field_name="draft")
        if payload is None:
            raise TypeError("draft must be provided")
        body = {
            "workspace_id": self.workspace_id,
            **payload,
        }
        response = self._request_json("POST", "/semantic/artifacts/drafts", json_body=body)
        return SemanticArtifact.from_dict(response)

    def approve_artifact(
        self,
        artifact_id: str,
        *,
        approved_by: str,
        approval_note: Optional[str] = None,
    ) -> SemanticArtifact:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "approved_by": approved_by,
        }
        if approval_note:
            body["approval_note"] = approval_note
        payload = self._request_json("POST", f"/semantic/artifacts/{artifact_id}/approve", json_body=body)
        return SemanticArtifact.from_dict(payload)

    def deprecate_artifact(
        self,
        artifact_id: str,
        *,
        deprecated_by: str,
        deprecation_note: Optional[str] = None,
    ) -> SemanticArtifact:
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "deprecated_by": deprecated_by,
        }
        if deprecation_note:
            body["deprecation_note"] = deprecation_note
        payload = self._request_json("POST", f"/semantic/artifacts/{artifact_id}/deprecate", json_body=body)
        return SemanticArtifact.from_dict(payload)

    def validate_artifact(
        self,
        artifact: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
    ) -> ArtifactValidationResult:
        return validate_artifact_payload(artifact)

    def diff_artifacts(
        self,
        left: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
        right: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
    ) -> ArtifactDiff:
        return diff_artifact_payloads(left, right)

    def close(self) -> None:
        self._graph_catalog_cache = None
        self._session.close()
        if self._local_mode and hasattr(self.graph_store, "close"):
            self.graph_store.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _scope_payload(
        self,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        resolved_user_id = user_id if user_id is not None else self.user_id
        resolved_agent_id = agent_id if agent_id is not None else self.agent_id
        resolved_session_id = session_id if session_id is not None else self.session_id
        if resolved_user_id:
            payload["user_id"] = resolved_user_id
        if resolved_agent_id:
            payload["agent_id"] = resolved_agent_id
        if resolved_session_id:
            payload["session_id"] = resolved_session_id
        return payload

    def _query_payload(
        self,
        *,
        query: str,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "query": query,
            "workspace_id": self.workspace_id,
            "user_id": user_id if user_id is not None else self.user_id or "user_default",
        }
        if graph_ids:
            payload["graph_ids"] = list(graph_ids)
        return payload

    @staticmethod
    def _serialize_entity_overrides(
        entity_overrides: Sequence[EntityOverride | Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        serialized: List[Dict[str, Any]] = []
        for item in entity_overrides:
            if isinstance(item, EntityOverride):
                serialized.append(item.to_dict())
            elif isinstance(item, dict):
                serialized.append(dict(item))
            else:
                raise TypeError("entity_overrides must contain dict objects or EntityOverride values")
        return serialized

    @staticmethod
    def _coerce_graph_ref(graph: GraphRef | GraphTarget | Dict[str, Any] | str) -> GraphRef:
        if isinstance(graph, GraphRef):
            return graph
        if isinstance(graph, GraphTarget):
            return GraphRef.from_graph_target(graph)
        if isinstance(graph, dict):
            return GraphRef.from_dict(graph)
        if isinstance(graph, str):
            return GraphRef(graph_id=graph)
        raise TypeError("graph references must be GraphRef, GraphTarget, dict, or str")

    @staticmethod
    def _coerce_entity_override(
        item: EntityOverride | Dict[str, Any],
    ) -> EntityOverride:
        if isinstance(item, EntityOverride):
            return item
        if isinstance(item, dict):
            return EntityOverride.from_dict(item)
        raise TypeError("entity_overrides must contain dict objects or EntityOverride values")

    def resolve_graphs(
        self,
        *graphs: GraphRef | GraphTarget | Dict[str, Any] | str,
        ontology_ids: Optional[Sequence[str]] = None,
        vocabulary_profiles: Optional[Sequence[str]] = None,
    ) -> List[GraphRef]:
        plan = ExecutionPlan(
            query="",
            targets=[self._coerce_graph_ref(graph) for graph in graphs],
            ontology_ids=[str(item).strip() for item in ontology_ids or [] if str(item).strip()],
            vocabulary_profiles=[
                str(item).strip() for item in vocabulary_profiles or [] if str(item).strip()
            ],
        )
        return self._resolve_execution_plan(plan).targets

    def _execute_local_plan(self, plan: ExecutionPlan) -> ExecutionResult:
        style = plan.reasoning.normalized_style()
        if style != "direct":
            raise RuntimeError(
                "react and debate execution plans require HTTP client mode; "
                "local engine mode currently supports direct execution only"
            )
        databases = plan.databases or [plan.graph_ids[0]] if plan.graph_ids else []
        database = databases[0] if databases else "neo4j"
        response = self.ask(plan.query, database=database, user_id=plan.user_id)
        return ExecutionResult(
            requested_style="direct",
            runtime_mode="semantic",
            response=response,
            resolved_targets=plan.targets,
            graph_ids=plan.graph_ids,
            databases=[database] if database else [],
            trace_steps=[],
        )

    def _resolve_execution_plan(self, plan: ExecutionPlan) -> ExecutionPlan:
        explicit_targets = [self._coerce_graph_ref(target) for target in plan.targets]
        catalog = self._graph_catalog() if not self._local_mode else {}
        resolved_targets = [
            self._merge_graph_ref(target, catalog.get(target.graph_id))
            for target in explicit_targets
        ]

        ontology_ids = [str(item).strip() for item in plan.ontology_ids if str(item).strip()]
        vocabulary_profiles = [
            str(item).strip() for item in plan.vocabulary_profiles if str(item).strip()
        ]

        if ontology_ids or vocabulary_profiles:
            if resolved_targets:
                candidates = resolved_targets
            else:
                candidates = [
                    GraphRef.from_graph_target(target)
                    for target in catalog.values()
                ]
            filtered_targets = [
                target
                for target in candidates
                if self._graph_matches_filters(target, ontology_ids, vocabulary_profiles)
            ]
            if explicit_targets and len(filtered_targets) != len(candidates):
                rejected = [
                    target.graph_id
                    for target in candidates
                    if not self._graph_matches_filters(target, ontology_ids, vocabulary_profiles)
                ]
                raise ValueError(
                    "Selected graph targets do not match the requested ontology/vocabulary filters: "
                    f"{rejected}"
                )
            if not filtered_targets:
                raise ValueError(
                    "No graph targets matched the requested ontology/vocabulary filters."
                )
            resolved_targets = filtered_targets

        resolved_overrides = [
            self._coerce_entity_override(item)
            for item in plan.entity_overrides
        ]

        return ExecutionPlan(
            query=plan.query,
            targets=resolved_targets,
            reasoning=plan.reasoning,
            entity_overrides=resolved_overrides,
            user_id=plan.user_id or self.user_id,
            session_id=plan.session_id or self.session_id,
            workspace_id=plan.workspace_id or self.workspace_id,
            ontology_ids=ontology_ids,
            vocabulary_profiles=vocabulary_profiles,
        )

    def _graph_catalog(self) -> Dict[str, GraphTarget]:
        if self._graph_catalog_cache is None:
            self._graph_catalog_cache = {
                target.graph_id: target
                for target in self.graphs()
            }
        return dict(self._graph_catalog_cache)

    @staticmethod
    def _graph_matches_filters(
        target: GraphRef,
        ontology_ids: Sequence[str],
        vocabulary_profiles: Sequence[str],
    ) -> bool:
        ontology_match = not ontology_ids or str(target.ontology_id or "") in set(ontology_ids)
        vocabulary_match = not vocabulary_profiles or str(target.vocabulary_profile or "") in set(vocabulary_profiles)
        return ontology_match and vocabulary_match

    @staticmethod
    def _merge_graph_ref(target: GraphRef, graph_target: Optional[GraphTarget]) -> GraphRef:
        if graph_target is None:
            return GraphRef(
                graph_id=target.graph_id,
                database=target.database or target.graph_id,
                ontology_id=target.ontology_id,
                vocabulary_profile=target.vocabulary_profile,
                description=target.description,
                workspace_scope=target.workspace_scope,
            )
        return GraphRef(
            graph_id=target.graph_id or graph_target.graph_id,
            database=target.database or graph_target.database,
            ontology_id=target.ontology_id or graph_target.ontology_id,
            vocabulary_profile=target.vocabulary_profile or graph_target.vocabulary_profile,
            description=target.description or graph_target.description,
            workspace_scope=target.workspace_scope or graph_target.workspace_scope,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self._session.request(
                method=method,
                url=url,
                json=json_body,
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SeochoConnectionError(f"Could not reach SEOCHO at {url}: {exc}") from exc

        if response.status_code >= 400:
            detail: Any
            try:
                payload = response.json()
                detail = payload.get("detail", payload)
            except ValueError:
                detail = response.text
            raise SeochoHTTPError(status_code=response.status_code, path=path, detail=detail)

        try:
            payload = response.json()
        except ValueError as exc:
            raise SeochoConnectionError(f"SEOCHO returned invalid JSON for {path}") from exc

        if not isinstance(payload, dict):
            raise SeochoConnectionError(f"SEOCHO returned unexpected payload for {path}")
        return payload


# ======================================================================
# Execution Plan Builder
# ======================================================================


class ExecutionPlanBuilder:
    """Chainable graph-centric execution plan builder for SDK callers."""

    def __init__(
        self,
        client: Seocho,
        query: str,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self._client = client
        self._query = query
        self._targets: List[GraphRef] = []
        self._reasoning = ReasoningPolicy()
        self._entity_overrides: List[EntityOverride] = []
        self._ontology_ids: List[str] = []
        self._vocabulary_profiles: List[str] = []
        self._user_id = user_id
        self._session_id = session_id

    def on_graph(self, graph: GraphRef | GraphTarget | Dict[str, Any] | str) -> "ExecutionPlanBuilder":
        return self.on_graphs(graph)

    def on_graphs(self, *graphs: GraphRef | GraphTarget | Dict[str, Any] | str) -> "ExecutionPlanBuilder":
        for graph in graphs:
            if isinstance(graph, (list, tuple)):
                for item in graph:
                    self._targets.append(self._client._coerce_graph_ref(item))
                continue
            self._targets.append(self._client._coerce_graph_ref(graph))
        return self

    def with_ontology(self, *ontology_ids: str) -> "ExecutionPlanBuilder":
        self._ontology_ids = [str(item).strip() for item in ontology_ids if str(item).strip()]
        return self

    def with_vocabulary(self, *vocabulary_profiles: str) -> "ExecutionPlanBuilder":
        self._vocabulary_profiles = [
            str(item).strip() for item in vocabulary_profiles if str(item).strip()
        ]
        return self

    def with_reasoning(
        self,
        *,
        style: str,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
        require_grounded_evidence: bool = True,
        repair_budget: Optional[int] = None,
        fallback_style: Optional[str] = None,
    ) -> "ExecutionPlanBuilder":
        self._reasoning = ReasoningPolicy(
            style=str(style).strip().lower() or "direct",
            max_steps=max_steps,
            tool_budget=tool_budget,
            require_grounded_evidence=require_grounded_evidence,
            repair_budget=(
                self._reasoning.repair_budget
                if repair_budget is None
                else max(0, int(repair_budget))
            ),
            fallback_style=(str(fallback_style).strip().lower() or None) if fallback_style else None,
        )
        self._reasoning.normalized_style()
        return self

    def direct(self) -> "ExecutionPlanBuilder":
        return self.with_reasoning(style="direct")

    def react(
        self,
        *,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
        require_grounded_evidence: bool = True,
        fallback_style: Optional[str] = None,
    ) -> "ExecutionPlanBuilder":
        return self.with_reasoning(
            style="react",
            max_steps=max_steps,
            tool_budget=tool_budget,
            require_grounded_evidence=require_grounded_evidence,
            fallback_style=fallback_style,
        )

    def debate(
        self,
        *,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
        require_grounded_evidence: bool = True,
        fallback_style: Optional[str] = None,
    ) -> "ExecutionPlanBuilder":
        return self.with_reasoning(
            style="debate",
            max_steps=max_steps,
            tool_budget=tool_budget,
            require_grounded_evidence=require_grounded_evidence,
            fallback_style=fallback_style,
        )

    def advanced(
        self,
        *,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
        require_grounded_evidence: bool = True,
        fallback_style: Optional[str] = None,
    ) -> "ExecutionPlanBuilder":
        return self.debate(
            max_steps=max_steps,
            tool_budget=tool_budget,
            require_grounded_evidence=require_grounded_evidence,
            fallback_style=fallback_style,
        )

    def with_repair_budget(self, repair_budget: int) -> "ExecutionPlanBuilder":
        self._reasoning = ReasoningPolicy(
            style=self._reasoning.normalized_style(),
            max_steps=self._reasoning.max_steps,
            tool_budget=self._reasoning.tool_budget,
            require_grounded_evidence=self._reasoning.require_grounded_evidence,
            repair_budget=max(0, int(repair_budget)),
            fallback_style=self._reasoning.fallback_style,
        )
        return self

    def with_entity_overrides(
        self,
        *entity_overrides: EntityOverride | Dict[str, Any],
    ) -> "ExecutionPlanBuilder":
        flattened: List[EntityOverride | Dict[str, Any]] = []
        for item in entity_overrides:
            if isinstance(item, (list, tuple)):
                flattened.extend(item)
            else:
                flattened.append(item)
        self._entity_overrides = [
            self._client._coerce_entity_override(item)
            for item in flattened
        ]
        return self

    def for_user(self, user_id: str) -> "ExecutionPlanBuilder":
        self._user_id = user_id
        return self

    def in_session(self, session_id: str) -> "ExecutionPlanBuilder":
        self._session_id = session_id
        return self

    def build(self) -> ExecutionPlan:
        return ExecutionPlan(
            query=self._query,
            targets=list(self._targets),
            reasoning=self._reasoning,
            entity_overrides=list(self._entity_overrides),
            user_id=self._user_id,
            session_id=self._session_id,
            workspace_id=self._client.workspace_id,
            ontology_ids=list(self._ontology_ids),
            vocabulary_profiles=list(self._vocabulary_profiles),
        )

    def run(self) -> ExecutionResult:
        return self._client.execute(self.build())


# ======================================================================
# Local Engine — orchestrates ontology + LLM + graph store in-process
# ======================================================================


class _LocalEngine:
    """Internal orchestrator for local engine mode.

    Wires together Ontology → IndexingPipeline → QueryStrategy → GraphStore.
    """

    def __init__(
        self,
        *,
        ontology: Any,  # Ontology
        graph_store: Any,  # GraphStore
        llm: Any,  # LLMBackend
        workspace_id: str,
        extraction_prompt: Optional[Any] = None,  # PromptTemplate
        agent_config: Optional[Any] = None,  # AgentConfig
    ) -> None:
        from .agent_config import AgentConfig
        from .indexing import IndexingPipeline
        from .ontology import Ontology
        from .prompt_strategy import ExtractionStrategy, LinkingStrategy, QueryStrategy

        self.ontology: Ontology = ontology
        self.graph_store = graph_store
        self.llm = llm
        self.workspace_id = workspace_id
        self.agent_config: AgentConfig = agent_config or AgentConfig()

        # Indexing pipeline (handles chunking, extraction, validation, dedup, write)
        self._indexing = IndexingPipeline(
            ontology=ontology, graph_store=graph_store,
            llm=llm, workspace_id=workspace_id,
        )

        # Pre-build strategies (for extract-only and query)
        self._extraction = ExtractionStrategy(ontology, prompt_template=extraction_prompt)
        self._linking = LinkingStrategy(ontology)
        self._query = QueryStrategy(ontology)

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
        """Chunk → Extract → Validate → Link → Write pipeline.

        Delegates to :class:`~seocho.indexing.IndexingPipeline` which
        handles automatic chunking for long documents, SHACL validation,
        cross-chunk deduplication, and content-hash dedup.

        If ``ontology_override`` is provided, it is used instead of the
        default ontology (for multi-ontology per database support).
        """
        pipeline = self._indexing
        if ontology_override is not None:
            from .indexing import IndexingPipeline
            pipeline = IndexingPipeline(
                ontology=ontology_override,
                graph_store=self.graph_store,
                llm=self.llm,
                workspace_id=self.workspace_id,
                strict_validation=strict_validation,
            )
        else:
            pipeline.strict_validation = strict_validation

        result = pipeline.index(
            content,
            database=database,
            category=category,
            metadata=metadata,
        )

        return Memory(
            memory_id=result.source_id,
            workspace_id=self.workspace_id,
            content=content[:500],
            metadata={
                "category": category,
                "nodes_created": result.total_nodes,
                "relationships_created": result.total_relationships,
                "chunks_processed": result.chunks_processed,
                "validation_errors": result.validation_errors,
                "write_errors": result.write_errors,
                "skipped_chunks": result.skipped_chunks,
                "deduplicated": result.deduplicated,
                **(metadata or {}),
            },
            status="active" if result.ok else "failed",
            database=database,
            category=category,
            source_type="text",
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
        """Index multiple documents with progress tracking.

        Returns a summary dict with per-document results.
        """
        from .indexing import BatchIndexingResult

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
        self._extraction.category = category
        system, user = self._extraction.render(content, metadata=metadata)

        response = self.llm.complete(
            system=system,
            user=user,
            temperature=0.0,
            response_format={"type": "json_object"},
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
        ontology_override: Optional[Any] = None,
    ) -> str:
        """Ontology-aware query: generate Cypher → execute → synthesize answer.

        If ``reasoning_mode`` or ``repair_budget`` are not provided,
        defaults from ``agent_config`` are used.

        Parameters
        ----------
        question:
            Natural-language question.
        database:
            Target database.
        reasoning_mode:
            If True, inspect query results and attempt repair when
            results are empty or insufficient (up to ``repair_budget``
            retries with progressively relaxed queries).
        repair_budget:
            Maximum number of repair attempts (only used when
            ``reasoning_mode=True``).  Each attempt costs one LLM call
            for query generation + one Cypher execution.
        """
        # Use database-specific ontology if registered
        active_ontology = ontology_override or self.ontology
        if ontology_override is not None:
            from .prompt_strategy import QueryStrategy
            self._query = QueryStrategy(active_ontology)

        # Apply agent_config defaults
        if reasoning_mode is None:
            reasoning_mode = self.agent_config.reasoning_mode
        if repair_budget is None:
            repair_budget = self.agent_config.repair_budget

        schema_info = self._get_schema_info(database)
        self._query.schema_info = schema_info

        # --- First attempt ---
        cypher, params, error = self._generate_cypher(question)
        if error:
            return error

        records, exec_error = self._execute_cypher(cypher, params, database)
        if exec_error:
            return exec_error

        # --- Reasoning mode: repair if results insufficient ---
        attempts = []
        if reasoning_mode and repair_budget > 0 and not records:
            attempts.append({"cypher": cypher, "result_count": 0, "error": None})

            for attempt_num in range(repair_budget):
                repair_cypher, repair_params, repair_error = self._generate_repair_query(
                    question, attempts, schema_info,
                )
                if repair_error or not repair_cypher:
                    break

                repair_records, repair_exec_error = self._execute_cypher(
                    repair_cypher, repair_params, database,
                )
                attempts.append({
                    "cypher": repair_cypher,
                    "result_count": len(repair_records) if repair_records else 0,
                    "error": repair_exec_error,
                })

                if repair_records:
                    records = repair_records
                    cypher = repair_cypher
                    break

        # --- Synthesize answer ---
        reasoning_trace = None
        if reasoning_mode and attempts:
            reasoning_trace = json.dumps(attempts, default=str)

        system_ans, user_ans = self._query.render_answer(
            question, json.dumps(records, default=str),
        )
        if reasoning_trace:
            user_ans += f"\n\nReasoning trace (query attempts):\n{reasoning_trace}"

        answer_response = self.llm.complete(
            system=system_ans, user=user_ans, temperature=0.1,
        )
        return answer_response.text

    def _get_schema_info(self, database: str) -> Dict[str, Any]:
        try:
            schema = self.graph_store.get_schema(database=database)
            return {
                "node_labels": ", ".join(schema.get("labels", [])),
                "relationship_types": ", ".join(schema.get("relationship_types", [])),
            }
        except Exception:
            return {}

    def _generate_cypher(self, question: str) -> tuple:
        """Extract intent via LLM, then build Cypher deterministically.

        Returns (cypher, params, error_message_or_None).
        """
        from .query.cypher_builder import CypherBuilder

        builder = CypherBuilder(self.ontology)

        # Step 1: LLM extracts intent (NOT Cypher)
        intent_prompt = builder.intent_extraction_prompt()
        response = self.llm.complete(
            system=intent_prompt,
            user=f"Question: {question}",
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        try:
            intent_data = response.json()
        except (json.JSONDecodeError, ValueError):
            logger.error("LLM returned non-JSON intent: %s", response.text[:200])
            # Fallback: treat as neighbor query for the entire question
            intent_data = {"intent": "neighbors", "anchor_entity": question}

        # Step 2: Code builds correct Cypher from intent
        try:
            cypher, params = builder.build(
                intent=intent_data.get("intent", "neighbors"),
                anchor_entity=intent_data.get("anchor_entity", question),
                anchor_label=intent_data.get("anchor_label", ""),
                target_entity=intent_data.get("target_entity", ""),
                target_label=intent_data.get("target_label", ""),
                relationship_type=intent_data.get("relationship_type", ""),
            )
        except Exception as exc:
            logger.error("Cypher build failed: %s", exc)
            return "", {}, "I could not build a query for your question."

        if not cypher:
            return "", {}, "I could not determine how to query the graph."
        return cypher, params, None

    def _execute_cypher(self, cypher: str, params: Dict, database: str) -> tuple:
        """Returns (records, error_message_or_None)."""
        try:
            records = self.graph_store.query(cypher, params=params, database=database)
            return records, None
        except Exception as exc:
            logger.error("Cypher execution failed: %s — query: %s", exc, cypher)
            return [], f"The query could not be executed: {exc}"

    def _generate_repair_query(
        self,
        question: str,
        attempts: List[Dict],
        schema_info: Dict[str, Any],
    ) -> tuple:
        """Generate a repaired Cypher query based on previous failed attempts."""
        ctx = self.ontology.to_query_context()
        attempts_summary = "\n".join(
            f"  Attempt {i+1}: {a['cypher'][:100]}... → {a['result_count']} results"
            + (f" (error: {a['error']})" if a.get("error") else "")
            for i, a in enumerate(attempts)
        )

        system = (
            "You are a knowledge graph query repair agent.\n"
            f"Working with ontology \"{ctx['ontology_name']}\".\n\n"
            f"--- Graph Schema ---\n{ctx['graph_schema']}\n\n"
            f"The previous queries returned no results:\n{attempts_summary}\n\n"
            "Generate a RELAXED alternative query that:\n"
            "- Uses broader match patterns (CONTAINS instead of exact match)\n"
            "- Tries alternative relationship paths\n"
            "- Removes overly specific filters\n"
            "- Falls back to listing available entities if all else fails\n\n"
            "Return JSON: {\"cypher\": \"...\", \"params\": {...}, \"strategy\": \"...\"}"
        )
        user = f"Original question: {question}"

        response = self.llm.complete(
            system=system, user=user,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        try:
            plan = response.json()
        except (json.JSONDecodeError, ValueError):
            return "", {}, "Repair query generation failed"

        return plan.get("cypher", ""), plan.get("params", {}), None

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

        response = self.llm.complete(
            system=system,
            user=user,
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return {"nodes": nodes, "relationships": relationships}


# ======================================================================
# AsyncSeocho
# ======================================================================


class AsyncSeocho:
    """Async wrapper around the sync client for notebook and app usage."""

    def __init__(self, **kwargs: Any) -> None:
        self._client = Seocho(**kwargs)

    async def add(self, content: str, **kwargs: Any) -> Memory:
        return await asyncio.to_thread(self._client.add, content, **kwargs)

    async def add_with_details(self, content: str, **kwargs: Any) -> MemoryCreateResult:
        return await asyncio.to_thread(self._client.add_with_details, content, **kwargs)

    async def apply_artifact(self, artifact_id: str, content: str, **kwargs: Any) -> MemoryCreateResult:
        return await asyncio.to_thread(self._client.apply_artifact, artifact_id, content, **kwargs)

    async def get(self, memory_id: str, **kwargs: Any) -> Memory:
        return await asyncio.to_thread(self._client.get, memory_id, **kwargs)

    async def search(self, query: str, **kwargs: Any) -> List[SearchResult]:
        return await asyncio.to_thread(self._client.search, query, **kwargs)

    async def search_with_context(self, query: str, **kwargs: Any) -> SearchResponse:
        return await asyncio.to_thread(self._client.search_with_context, query, **kwargs)

    async def ask(self, message: str, **kwargs: Any) -> str:
        return await asyncio.to_thread(self._client.ask, message, **kwargs)

    async def chat(self, message: str, **kwargs: Any) -> ChatResponse:
        return await asyncio.to_thread(self._client.chat, message, **kwargs)

    async def delete(self, memory_id: str, **kwargs: Any) -> ArchiveResult:
        return await asyncio.to_thread(self._client.delete, memory_id, **kwargs)

    async def extract(self, content: str, **kwargs: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(self._client.extract, content, **kwargs)

    async def query(self, cypher: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._client.query, cypher, **kwargs)

    async def router(self, query: str, **kwargs: Any) -> AgentRunResponse:
        return await asyncio.to_thread(self._client.router, query, **kwargs)

    async def react(self, query: str, **kwargs: Any) -> AgentRunResponse:
        return await asyncio.to_thread(self._client.react, query, **kwargs)

    async def advanced(self, query: str, **kwargs: Any) -> DebateRunResponse:
        return await asyncio.to_thread(self._client.advanced, query, **kwargs)

    async def semantic(self, query: str, **kwargs: Any) -> SemanticRunResponse:
        return await asyncio.to_thread(self._client.semantic, query, **kwargs)

    async def debate(self, query: str, **kwargs: Any) -> DebateRunResponse:
        return await asyncio.to_thread(self._client.debate, query, **kwargs)

    async def execute(self, plan: ExecutionPlan | Dict[str, Any]) -> ExecutionResult:
        return await asyncio.to_thread(self._client.execute, plan)

    async def platform_chat(self, message: str, **kwargs: Any) -> PlatformChatResponse:
        return await asyncio.to_thread(self._client.platform_chat, message, **kwargs)

    async def session_history(self, session_id: str) -> PlatformSessionResponse:
        return await asyncio.to_thread(self._client.session_history, session_id)

    async def reset_session(self, session_id: str) -> PlatformSessionResponse:
        return await asyncio.to_thread(self._client.reset_session, session_id)

    async def raw_ingest(self, records: Sequence[Dict[str, Any]], **kwargs: Any) -> RawIngestResult:
        return await asyncio.to_thread(self._client.raw_ingest, records, **kwargs)

    async def graphs(self) -> List[GraphTarget]:
        return await asyncio.to_thread(self._client.graphs)

    async def databases(self) -> List[str]:
        return await asyncio.to_thread(self._client.databases)

    async def agents(self) -> List[str]:
        return await asyncio.to_thread(self._client.agents)

    async def health(self, *, scope: str = "runtime") -> Dict[str, Any]:
        return await asyncio.to_thread(self._client.health, scope=scope)

    async def semantic_runs(
        self,
        *,
        limit: int = 20,
        route: Optional[str] = None,
        intent_id: Optional[str] = None,
    ) -> List[SemanticRunRecord]:
        return await asyncio.to_thread(
            self._client.semantic_runs,
            limit=limit,
            route=route,
            intent_id=intent_id,
        )

    async def semantic_run(self, run_id: str) -> SemanticRunRecord:
        return await asyncio.to_thread(self._client.semantic_run, run_id)

    async def ensure_fulltext_indexes(self, **kwargs: Any) -> FulltextIndexResponse:
        return await asyncio.to_thread(self._client.ensure_fulltext_indexes, **kwargs)

    async def list_artifacts(self, *, status: Optional[str] = None) -> List[SemanticArtifactSummary]:
        return await asyncio.to_thread(self._client.list_artifacts, status=status)

    async def get_artifact(self, artifact_id: str) -> SemanticArtifact:
        return await asyncio.to_thread(self._client.get_artifact, artifact_id)

    async def create_artifact_draft(
        self,
        draft: SemanticArtifactDraftInput | Dict[str, Any],
    ) -> SemanticArtifact:
        return await asyncio.to_thread(self._client.create_artifact_draft, draft)

    async def approve_artifact(
        self,
        artifact_id: str,
        *,
        approved_by: str,
        approval_note: Optional[str] = None,
    ) -> SemanticArtifact:
        return await asyncio.to_thread(
            self._client.approve_artifact,
            artifact_id,
            approved_by=approved_by,
            approval_note=approval_note,
        )

    async def deprecate_artifact(
        self,
        artifact_id: str,
        *,
        deprecated_by: str,
        deprecation_note: Optional[str] = None,
    ) -> SemanticArtifact:
        return await asyncio.to_thread(
            self._client.deprecate_artifact,
            artifact_id,
            deprecated_by=deprecated_by,
            deprecation_note=deprecation_note,
        )

    async def validate_artifact(
        self,
        artifact: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
    ) -> ArtifactValidationResult:
        return await asyncio.to_thread(self._client.validate_artifact, artifact)

    async def diff_artifacts(
        self,
        left: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
        right: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
    ) -> ArtifactDiff:
        return await asyncio.to_thread(self._client.diff_artifacts, left, right)

    async def aclose(self) -> None:
        await asyncio.to_thread(self._client.close)
