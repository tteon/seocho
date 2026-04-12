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
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence
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
from .runtime_contract import (
    RuntimePath,
    build_query_payload,
    build_scope_payload,
    memory_path,
    platform_chat_session_path,
    semantic_run_path,
    serialize_entity_overrides,
)
if TYPE_CHECKING:
    from .runtime_bundle import RuntimeBundle

logger = logging.getLogger(__name__)
_FOUR_DIGIT_YEAR_RE = re.compile(r"\b(20\d{2})\b")


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
        """Initialize the Seocho client.

        Provide ``ontology``, ``graph_store``, and ``llm`` for local
        engine mode.  Otherwise, the client connects to a SEOCHO HTTP
        server at ``base_url``.

        Args:
            ontology: Ontology schema for extraction and querying.
            graph_store: Graph database backend (e.g. Neo4jGraphStore).
            llm: LLM backend for extraction and synthesis.
            vector_store: Optional vector store for hybrid search.
            extraction_prompt: Custom extraction prompt template.
            agent_config: Agent-level configuration (quality thresholds, reasoning defaults).
            base_url: SEOCHO server URL (HTTP mode). Defaults to ``SEOCHO_BASE_URL`` env
                var or ``http://localhost:8001``.
            workspace_id: Workspace identifier propagated to all API calls.
            user_id: Default user ID for scoped operations.
            agent_id: Default agent ID for scoped operations.
            session_id: Default session ID for scoped operations.
            timeout: HTTP request timeout in seconds.
            session: Optional ``requests.Session`` for connection pooling.
        """
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

        # Default database — auto-generated from ontology if not specified
        self.default_database = self._resolve_default_database(ontology)

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
            db = database or self.default_database
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
            db = database or (databases[0] if databases and len(databases) > 0 else self.default_database)
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

    # ------------------------------------------------------------------
    # Agent-level session API
    # ------------------------------------------------------------------

    def session(
        self,
        name: str = "",
        *,
        database: Optional[str] = None,
    ) -> "Session":
        """Create an agent-level session with context and tracing.

        A session maintains state across ``add()`` and ``ask()`` calls.
        Each operation prefers the agent/tool path, falls back to the
        canonical local engine when the agent path is unavailable, and
        rolls all operations into a single parent trace in Opik.

        Parameters
        ----------
        name:
            Session name for identification and tracing.
        database:
            Default target database for this session.

        Returns
        -------
        A :class:`~seocho.session.Session` that can be used as a
        context manager::

            with s.session("my_analysis") as sess:
                sess.add("Samsung's CEO is Jay Y. Lee.")
                answer = sess.ask("Who is Samsung's CEO?")

        Requires local engine mode (ontology + graph_store + llm).
        """
        if not self._local_mode:
            raise RuntimeError(
                "session() requires local engine mode. "
                "Provide ontology, graph_store, and llm to Seocho()."
            )

        from .session import Session

        return Session(
            name=name,
            ontology=self.ontology,
            graph_store=self.graph_store,
            llm=self.llm,
            vector_store=self.vector_store,
            database=database or self.default_database,
            extraction_prompt=self.extraction_prompt,
            agent_config=self.agent_config,
            workspace_id=self.workspace_id,
        )

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
        """Add content and return the full creation result including memory and artifacts.

        Unlike :meth:`add`, this returns a :class:`MemoryCreateResult` containing
        the created memory, any generated semantic artifacts, and server-side
        metadata.  HTTP mode only.

        Args:
            content: Text to ingest into the knowledge graph.
            metadata: Arbitrary metadata attached to the memory.
            prompt_context: Semantic prompt context for extraction guidance.
            database: Target database name.
            category: Content category (e.g. ``"memory"``, ``"general"``).
            source_type: Source type hint (e.g. ``"text"``, ``"file"``).
            semantic_artifact_policy: Artifact generation policy (``"auto"``, ``"approved_only"``, ``"none"``).
            approved_artifacts: Pre-approved artifact definitions to apply.
            approved_artifact_id: ID of a previously approved artifact to apply.

        Returns:
            A :class:`MemoryCreateResult` with the created memory and any artifacts.
        """
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
        body.update(
            build_scope_payload(
                default_user_id=self.user_id,
                default_agent_id=self.agent_id,
                default_session_id=self.session_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            )
        )
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
        payload = self._request_json("POST", RuntimePath.API_MEMORIES, json_body=body)
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
        """Add content using a specific approved semantic artifact.

        Convenience wrapper around :meth:`add_with_details` that forces
        ``semantic_artifact_policy="approved_only"`` and sets the
        ``approved_artifact_id``.

        Args:
            artifact_id: The ID of the approved artifact to apply.
            content: Text to ingest.

        Returns:
            A :class:`MemoryCreateResult` with the created memory.
        """
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
        """Retrieve a single memory by ID.

        Args:
            memory_id: The memory identifier returned from :meth:`add`.
            database: Optional target database override.

        Returns:
            The :class:`Memory` object.
        """
        params: Dict[str, Any] = {"workspace_id": self.workspace_id}
        if database:
            params["database"] = database
        payload = self._request_json("GET", memory_path(memory_id), params=params)
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
        """Search memories by natural-language query.

        Returns only the result list.  Use :meth:`search_with_context` for
        the full response including metadata.  HTTP mode only.

        Args:
            query: Natural-language search query.
            limit: Maximum number of results to return.

        Returns:
            List of :class:`SearchResult` objects ranked by relevance.
        """
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
        """Search memories and return the full response with context metadata.

        Args:
            query: Natural-language search query.
            limit: Maximum number of results to return.

        Returns:
            A :class:`SearchResponse` containing results and search metadata.
        """
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "query": query,
            "limit": limit,
        }
        body.update(
            build_scope_payload(
                default_user_id=self.user_id,
                default_agent_id=self.agent_id,
                default_session_id=self.session_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            )
        )
        if graph_ids:
            body["graph_ids"] = list(graph_ids)
        if databases:
            body["databases"] = list(databases)
        payload = self._request_json("POST", RuntimePath.API_MEMORIES_SEARCH, json_body=body)
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
        """Send a chat message and receive a graph-grounded response.

        Combines memory search with LLM synthesis to produce an answer
        grounded in the knowledge graph.  HTTP mode only.

        Args:
            message: Natural-language message or question.
            limit: Maximum number of memory results to consider.

        Returns:
            A :class:`ChatResponse` with the assistant message and sources.
        """
        body: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "message": message,
            "limit": limit,
        }
        body.update(
            build_scope_payload(
                default_user_id=self.user_id,
                default_agent_id=self.agent_id,
                default_session_id=self.session_id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            )
        )
        if graph_ids:
            body["graph_ids"] = list(graph_ids)
        if databases:
            body["databases"] = list(databases)
        payload = self._request_json("POST", RuntimePath.API_CHAT, json_body=body)
        return ChatResponse.from_dict(payload)

    def delete(self, memory_id: str, *, database: Optional[str] = None) -> ArchiveResult:
        """Archive (soft-delete) a memory by ID.

        Args:
            memory_id: The memory identifier to archive.
            database: Optional target database override.

        Returns:
            An :class:`ArchiveResult` confirming the operation.
        """
        params: Dict[str, Any] = {"workspace_id": self.workspace_id}
        if database:
            params["database"] = database
        payload = self._request_json("DELETE", memory_path(memory_id), params=params)
        return ArchiveResult.from_dict(payload)

    def router(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
    ) -> AgentRunResponse:
        """Run the graph-scoped tool-using router agent.

        The router agent selects the best graph(s) and tools to answer
        the query.  HTTP mode only.

        Args:
            query: Natural-language query.
            user_id: Override the default user ID for this call.
            graph_ids: Restrict routing to specific graph IDs.

        Returns:
            An :class:`AgentRunResponse` with the agent's answer and trace.
        """
        body = build_query_payload(
            query=query,
            workspace_id=self.workspace_id,
            default_user_id=self.user_id,
            user_id=user_id,
            graph_ids=graph_ids,
        )
        payload = self._request_json("POST", RuntimePath.RUN_AGENT, json_body=body)
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
        """Run the semantic query path with ontology-aware Cypher generation.

        Resolves graph targets, generates Cypher from the ontology, executes
        against the graph store, and synthesizes an answer.  Supports
        entity overrides and reasoning mode with query repair.  HTTP mode only.

        Args:
            query: Natural-language query.
            graph_ids: Graph references to query against (strings, GraphRef, GraphTarget, or dicts).
            databases: Explicit database names to target.
            entity_overrides: Entity disambiguation hints.
            reasoning_mode: Enable automatic query repair on empty results.
            repair_budget: Maximum repair attempts when reasoning_mode is enabled.

        Returns:
            A :class:`SemanticRunResponse` with the answer, Cypher, and trace.
        """
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
        body = build_query_payload(
            query=query,
            workspace_id=self.workspace_id,
            default_user_id=self.user_id,
            user_id=user_id,
            graph_ids=resolved_graph_ids,
        )
        if resolved_databases:
            body["databases"] = resolved_databases
        if entity_overrides:
            body["entity_overrides"] = serialize_entity_overrides(entity_overrides)
        if reasoning_mode:
            body["reasoning_mode"] = True
        if repair_budget > 0:
            body["repair_budget"] = int(repair_budget)
        payload = self._request_json("POST", RuntimePath.RUN_AGENT_SEMANTIC, json_body=body)
        return SemanticRunResponse.from_dict(payload)

    def debate(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[GraphRef | GraphTarget | Dict[str, Any] | str]] = None,
    ) -> DebateRunResponse:
        """Run the multi-agent debate path for complex queries.

        Multiple agents debate over the graph evidence to produce a
        synthesized, higher-confidence answer.  HTTP mode only.

        Args:
            query: Natural-language query.
            graph_ids: Graph references to debate over.

        Returns:
            A :class:`DebateRunResponse` with the consensus answer and debate trace.
        """
        resolved_graph_ids: Optional[List[str]] = None
        if graph_ids:
            plain_graph_ids = [str(item).strip() for item in graph_ids if isinstance(item, str) and str(item).strip()]
            if len(plain_graph_ids) == len(graph_ids):
                resolved_graph_ids = plain_graph_ids
            else:
                inline_targets = [self._coerce_graph_ref(item) for item in graph_ids]
                resolved_targets = inline_targets if all(target.database for target in inline_targets) else self.resolve_graphs(*graph_ids)
                resolved_graph_ids = [target.graph_id for target in resolved_targets if target.graph_id]
        body = build_query_payload(
            query=query,
            workspace_id=self.workspace_id,
            default_user_id=self.user_id,
            user_id=user_id,
            graph_ids=resolved_graph_ids,
        )
        payload = self._request_json("POST", RuntimePath.RUN_DEBATE, json_body=body)
        return DebateRunResponse.from_dict(payload)

    def plan(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> "ExecutionPlanBuilder":
        """Create a chainable execution plan builder for a query.

        Use the returned builder to configure graph targets, reasoning
        style, entity overrides, and then call ``.run()`` to execute.

        Example::

            result = (
                s.plan("Who is Samsung's CEO?")
                .on_graph("news_kg")
                .direct()
                .run()
            )

        Args:
            query: Natural-language query to plan execution for.

        Returns:
            An :class:`ExecutionPlanBuilder` for fluent configuration.
        """
        return ExecutionPlanBuilder(
            self,
            query,
            user_id=user_id if user_id is not None else self.user_id,
            session_id=session_id if session_id is not None else self.session_id,
        )

    def execute(self, plan: ExecutionPlan | Dict[str, Any]) -> ExecutionResult:
        """Execute a fully built execution plan.

        Resolves graph targets, selects the runtime path based on
        reasoning style (direct/react/debate), and returns the result.

        Args:
            plan: An :class:`ExecutionPlan` or compatible dict.

        Returns:
            An :class:`ExecutionResult` with the answer and execution metadata.
        """
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
        """Send a message through the platform chat endpoint.

        Supports multiple modes (``"semantic"``, ``"debate"``, ``"react"``)
        and maintains server-side session state.  HTTP mode only.

        Args:
            message: Natural-language message.
            mode: Execution mode (``"semantic"``, ``"debate"``, ``"react"``).
            session_id: Chat session identifier for conversation continuity.
            entity_overrides: Entity disambiguation hints.

        Returns:
            A :class:`PlatformChatResponse` with the assistant reply.
        """
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
            body["entity_overrides"] = serialize_entity_overrides(entity_overrides)
        payload = self._request_json("POST", RuntimePath.PLATFORM_CHAT_SEND, json_body=body)
        return PlatformChatResponse.from_dict(payload)

    def session_history(self, session_id: str) -> PlatformSessionResponse:
        """Retrieve the message history for a platform chat session.

        Args:
            session_id: The session identifier.

        Returns:
            A :class:`PlatformSessionResponse` with the conversation history.
        """
        payload = self._request_json("GET", platform_chat_session_path(session_id))
        return PlatformSessionResponse.from_dict(payload)

    def reset_session(self, session_id: str) -> PlatformSessionResponse:
        """Clear the message history for a platform chat session.

        Args:
            session_id: The session identifier to reset.

        Returns:
            A :class:`PlatformSessionResponse` confirming the reset.
        """
        payload = self._request_json("DELETE", platform_chat_session_path(session_id))
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
        """Ingest raw records directly into a target database.

        Bypasses the text extraction pipeline and writes structured records
        (nodes/relationships) directly to the graph.  Supports optional
        rule constraint validation and automatic database creation.

        Args:
            records: Sequence of record dicts to ingest.
            target_database: Database to write into.
            enable_rule_constraints: Validate records against inferred rules.
            create_database_if_missing: Auto-create the database if it does not exist.
            semantic_artifact_policy: Artifact generation policy.
            approved_artifacts: Pre-approved artifact definitions.
            approved_artifact_id: ID of a previously approved artifact.

        Returns:
            A :class:`RawIngestResult` with ingestion statistics.
        """
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
        payload = self._request_json("POST", RuntimePath.PLATFORM_INGEST_RAW, json_body=body)
        return RawIngestResult.from_dict(payload)

    def graphs(self) -> List[GraphTarget]:
        """List all available graph targets from the server catalog.

        Results are cached internally for graph resolution in subsequent calls.

        Returns:
            List of :class:`GraphTarget` objects.
        """
        payload = self._request_json("GET", RuntimePath.GRAPHS)
        graphs = [GraphTarget.from_dict(item) for item in payload.get("graphs", [])]
        self._graph_catalog_cache = {target.graph_id: target for target in graphs}
        return graphs

    def databases(self) -> List[str]:
        """List all available database names from the server.

        Returns:
            List of database name strings.
        """
        payload = self._request_json("GET", RuntimePath.DATABASES)
        return [str(item) for item in payload.get("databases", [])]

    def agents(self) -> List[str]:
        """List all registered agent names from the server.

        Returns:
            List of agent name strings.
        """
        payload = self._request_json("GET", RuntimePath.AGENTS)
        return [str(item) for item in payload.get("agents", [])]

    def health(self, *, scope: str = "runtime") -> Dict[str, Any]:
        """Check server health status.

        Args:
            scope: Health check scope (``"runtime"`` or ``"batch"``).

        Returns:
            Dict with health status fields.
        """
        if scope == "runtime":
            path = RuntimePath.HEALTH_RUNTIME
        elif scope == "batch":
            path = RuntimePath.HEALTH_BATCH
        else:
            path = f"/health/{scope}"
        return self._request_json("GET", path)

    def semantic_runs(
        self,
        *,
        limit: int = 20,
        route: Optional[str] = None,
        intent_id: Optional[str] = None,
    ) -> List[SemanticRunRecord]:
        """List recent semantic run records.

        Args:
            limit: Maximum number of records to return.
            route: Filter by route name.
            intent_id: Filter by intent ID.

        Returns:
            List of :class:`SemanticRunRecord` objects.
        """
        params: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "limit": max(1, int(limit or 20)),
        }
        if route:
            params["route"] = route
        if intent_id:
            params["intent_id"] = intent_id
        payload = self._request_json("GET", RuntimePath.SEMANTIC_RUNS, params=params)
        return [SemanticRunRecord.from_dict(item) for item in payload.get("runs", [])]

    def semantic_run(self, run_id: str) -> SemanticRunRecord:
        """Retrieve a single semantic run record by ID.

        Args:
            run_id: The semantic run identifier.

        Returns:
            A :class:`SemanticRunRecord` with full run details.
        """
        params: Dict[str, Any] = {"workspace_id": self.workspace_id}
        payload = self._request_json("GET", semantic_run_path(run_id), params=params)
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
        """Ensure fulltext indexes exist on the specified databases.

        Creates or verifies fulltext indexes for entity search.  HTTP mode only.

        Args:
            databases: Database names to index. Defaults to all databases.
            index_name: Name of the fulltext index.
            labels: Node labels to include in the index.
            properties: Node properties to index.
            create_if_missing: Create the index if it does not already exist.

        Returns:
            A :class:`FulltextIndexResponse` with index status per database.
        """
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
        payload = self._request_json("POST", RuntimePath.INDEXES_FULLTEXT_ENSURE, json_body=body)
        return FulltextIndexResponse.from_dict(payload)

    def list_artifacts(self, *, status: Optional[str] = None) -> List[SemanticArtifactSummary]:
        """List semantic artifacts, optionally filtered by status.

        Args:
            status: Filter by artifact status (e.g. ``"draft"``, ``"approved"``).

        Returns:
            List of :class:`SemanticArtifactSummary` objects.
        """
        params: Dict[str, Any] = {"workspace_id": self.workspace_id}
        if status:
            params["status"] = status
        payload = self._request_json("GET", "/semantic/artifacts", params=params)
        return [SemanticArtifactSummary.from_dict(item) for item in payload.get("artifacts", [])]

    def get_artifact(self, artifact_id: str) -> SemanticArtifact:
        """Retrieve a semantic artifact by ID.

        Args:
            artifact_id: The artifact identifier.

        Returns:
            The full :class:`SemanticArtifact` object.
        """
        params = {"workspace_id": self.workspace_id}
        payload = self._request_json("GET", f"/semantic/artifacts/{artifact_id}", params=params)
        return SemanticArtifact.from_dict(payload)

    def create_artifact_draft(
        self,
        draft: SemanticArtifactDraftInput | Dict[str, Any],
    ) -> SemanticArtifact:
        """Create a new semantic artifact draft.

        Args:
            draft: Artifact definition as a :class:`SemanticArtifactDraftInput` or dict.

        Returns:
            The created :class:`SemanticArtifact` in draft status.
        """
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
        """Approve a semantic artifact draft, promoting it to approved status.

        Args:
            artifact_id: The artifact to approve.
            approved_by: Identity of the approver.
            approval_note: Optional note explaining the approval.

        Returns:
            The updated :class:`SemanticArtifact` in approved status.
        """
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
        """Deprecate a semantic artifact, marking it as no longer recommended.

        Args:
            artifact_id: The artifact to deprecate.
            deprecated_by: Identity of the person deprecating.
            deprecation_note: Optional note explaining the deprecation.

        Returns:
            The updated :class:`SemanticArtifact` in deprecated status.
        """
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
        """Validate a semantic artifact payload locally.

        Checks required fields, schema consistency, and governance rules
        without contacting the server.

        Args:
            artifact: The artifact to validate.

        Returns:
            An :class:`ArtifactValidationResult` with any validation errors.
        """
        return validate_artifact_payload(artifact)

    def diff_artifacts(
        self,
        left: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
        right: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
    ) -> ArtifactDiff:
        """Compute the diff between two semantic artifact payloads.

        Useful for reviewing changes before approving a new artifact version.

        Args:
            left: The baseline artifact.
            right: The artifact to compare against.

        Returns:
            An :class:`ArtifactDiff` describing added, removed, and changed fields.
        """
        return diff_artifact_payloads(left, right)

    def export_runtime_bundle(
        self,
        path: Optional[str] = None,
        *,
        app_name: Optional[str] = None,
        default_database: str = "neo4j",
    ) -> "RuntimeBundle":
        """Export the client configuration as a portable runtime bundle.

        A runtime bundle captures ontology, artifacts, and configuration
        so that another environment can recreate an equivalent client.

        Args:
            path: If provided, save the bundle to this file path.
            app_name: Application name embedded in the bundle.
            default_database: Default database for the bundle.

        Returns:
            A :class:`RuntimeBundle` that can be saved or used directly.
        """
        from .runtime_bundle import build_runtime_bundle

        bundle = build_runtime_bundle(
            self,
            app_name=app_name,
            default_database=default_database,
        )
        if path:
            bundle.save(path)
        return bundle

    @classmethod
    def from_runtime_bundle(
        cls,
        bundle_source: "RuntimeBundle | str",
        *,
        workspace_id: Optional[str] = None,
    ) -> "Seocho":
        """Create a Seocho client from a saved runtime bundle.

        Args:
            bundle_source: A :class:`RuntimeBundle` object or path to a saved bundle file.
            workspace_id: Override the workspace ID from the bundle.

        Returns:
            A configured :class:`Seocho` client.
        """
        from .runtime_bundle import create_client_from_runtime_bundle

        return create_client_from_runtime_bundle(bundle_source, workspace_id=workspace_id)

    def close(self) -> None:
        """Release resources held by the client.

        Closes the HTTP session and, in local mode, the graph store connection.
        """
        self._graph_catalog_cache = None
        self._session.close()
        if self._local_mode and hasattr(self.graph_store, "close"):
            self.graph_store.close()

    @staticmethod
    def _resolve_default_database(ontology: Optional[Any]) -> str:
        """Generate an agent-friendly database name from ontology.

        Naming convention: {domain}{graphmodel}
        - agent가 이름만 보고 도메인과 그래프 모델을 알 수 있음
        - 예: financelpg, legallpg, newsrdf, fibordf

        Falls back to 'neo4j' if no ontology is provided.
        """
        if ontology is None:
            return "neo4j"

        from .store.graph import sanitize_database_name

        domain = ontology.name.lower().replace(" ", "").replace("-", "").replace("_", "")
        model = getattr(ontology, "graph_model", "lpg") or "lpg"
        raw = f"{domain}{model}"
        return sanitize_database_name(raw)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

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
        """Resolve graph references against the server catalog.

        Merges user-provided graph references with catalog metadata,
        optionally filtering by ontology or vocabulary profile.

        Args:
            graphs: Graph references to resolve (strings, GraphRef, GraphTarget, or dicts).
            ontology_ids: Filter to graphs matching these ontology IDs.
            vocabulary_profiles: Filter to graphs matching these vocabulary profiles.

        Returns:
            List of fully resolved :class:`GraphRef` objects.
        """
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
        self.extraction_prompt = extraction_prompt

        # Indexing pipeline (handles chunking, extraction, validation, dedup, write)
        self._indexing = IndexingPipeline(
            ontology=ontology, graph_store=graph_store,
            llm=llm, workspace_id=workspace_id,
            extraction_prompt=extraction_prompt,
        )
        # Pass AgentConfig quality settings to pipeline
        self._indexing._quality_threshold = self.agent_config.extraction_quality_threshold
        self._indexing._max_retries = self.agent_config.extraction_max_retries

        # Pre-build strategies (for extract-only and query)
        self._extraction = ExtractionStrategy(ontology, extraction_prompt=extraction_prompt)
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
                extraction_prompt=self.extraction_prompt,
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

        import time as _time
        _query_start = _time.time()

        schema_info = self._get_schema_info(database)
        self._query.schema_info = schema_info

        # --- First attempt ---
        cypher, params, intent_data, error = self._generate_cypher(question)
        if error:
            return error

        records, exec_error = self._execute_cypher(cypher, params, database)
        if exec_error:
            return exec_error

        # --- Auto-fallback to neighbors if relationship/entity lookup returns empty ---
        if not records and intent_data.get("intent") in ("relationship_lookup", "entity_lookup"):
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

        # --- Reasoning mode: repair if results insufficient ---
        attempts = []
        if reasoning_mode and repair_budget > 0 and not records:
            attempts.append({"cypher": cypher, "result_count": 0, "error": None})

            for attempt_num in range(repair_budget):
                repair_cypher, repair_params, repair_error = self._generate_repair_query(
                    question, attempts, schema_info, intent_data,
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

        # --- Vector hybrid fallback: if Cypher returns nothing, try vector search ---
        vector_context = ""
        if not records and hasattr(self, '_vector_store') and self._vector_store is not None:
            try:
                from .vector_store import VectorStore
                vs = self._vector_store
                if hasattr(vs, 'search'):
                    vresults = vs.search(question, limit=3)
                    if vresults:
                        vector_context = "\n".join(
                            f"[Vector result] {r.text[:300]}" for r in vresults
                        )
            except Exception:
                pass

        deterministic_answer = self._build_deterministic_answer(question, records, intent_data)
        if deterministic_answer:
            return deterministic_answer

        # --- Synthesize answer ---
        reasoning_trace = None
        if reasoning_mode and attempts:
            reasoning_trace = json.dumps(attempts, default=str)

        system_ans, user_ans = self._query.render_answer(
            question, json.dumps(records, default=str),
        )
        if reasoning_trace:
            user_ans += f"\n\nReasoning trace (query attempts):\n{reasoning_trace}"
        if vector_context:
            user_ans += f"\n\nAdditional context from vector search:\n{vector_context}"

        answer_response = self.llm.complete(
            system=system_ans, user=user_ans, temperature=0.1,
        )

        # --- Query tracing ---
        _query_elapsed = _time.time() - _query_start
        try:
            from .tracing import log_query, is_tracing_enabled
            if is_tracing_enabled():
                log_query(
                    question=question,
                    ontology_name=active_ontology.name,
                    model=getattr(self.llm, "model", "unknown"),
                    cypher=cypher,
                    result_count=len(records) if records else 0,
                    reasoning_attempts=len(attempts) if reasoning_mode and attempts else 0,
                    elapsed_seconds=_query_elapsed,
                )
        except Exception:
            pass

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

        Returns (cypher, params, intent_data, error_message_or_None).
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
            intent_data = {"intent": "neighbors", "anchor_entity": question}

        intent_data = builder.normalize_intent(question, intent_data)

        # Step 2: Code builds correct Cypher from intent
        try:
            cypher, params = builder.build(
                intent=intent_data.get("intent", "neighbors"),
                anchor_entity=intent_data.get("anchor_entity", question),
                anchor_label=intent_data.get("anchor_label", ""),
                target_entity=intent_data.get("target_entity", ""),
                target_label=intent_data.get("target_label", ""),
                relationship_type=intent_data.get("relationship_type", ""),
                metric_name=intent_data.get("metric_name", ""),
                metric_aliases=intent_data.get("metric_aliases", ()),
                metric_scope_tokens=intent_data.get("metric_scope_tokens", ()),
                years=intent_data.get("years", ()),
                workspace_id=self.workspace_id,
            )
        except Exception as exc:
            logger.error("Cypher build failed: %s", exc)
            return "", {}, intent_data, "I could not build a query for your question."

        if not cypher:
            return "", {}, intent_data, "I could not determine how to query the graph."
        return cypher, params, intent_data, None

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
        intent_data: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Generate a repaired Cypher query based on previous failed attempts."""
        if intent_data and str(intent_data.get("intent", "")).startswith("financial_metric_"):
            return "", {}, "Deterministic finance query returned no supported evidence."

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

    def _build_deterministic_answer(
        self,
        question: str,
        records: Sequence[Dict[str, Any]],
        intent_data: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if not intent_data:
            return None
        intent = str(intent_data.get("intent", "")).strip()
        if intent not in {"financial_metric_lookup", "financial_metric_delta"}:
            return None
        return self._build_financial_answer(question, records, intent_data)

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
