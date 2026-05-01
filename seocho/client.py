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
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

import requests

from .client_bundle import RuntimeBundleClientHelper
from .client_artifacts import (
    approved_artifacts_from_ontology as build_approved_artifacts_from_ontology,
)
from .client_artifacts import (
    artifact_draft_from_ontology as build_artifact_draft_from_ontology,
)
from .client_artifacts import (
    prompt_context_from_ontology as build_prompt_context_from_ontology,
)
from .client_remote import RemoteClientHelper
from .exceptions import SeochoConnectionError, SeochoHTTPError
from .governance import ArtifactDiff, ArtifactValidationResult, diff_artifact_payloads, validate_artifact_payload
from .local_engine import _LocalEngine
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
        ontology_profile: str = "default",
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
            ontology_profile: Stable context profile name shared by indexing, query, and agent runs.
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
        self.ontology_profile = str(ontology_profile or "default")

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
                ontology_profile=self.ontology_profile,
            )
            self._session = session or requests.Session()
        else:
            self._engine = None
            self._session = session or requests.Session()

        self._bundle_helper = RuntimeBundleClientHelper()
        resolved_base_url = ""
        if not self._local_mode:
            resolved_base_url = (base_url or _env_str("SEOCHO_BASE_URL", "http://localhost:8001")).rstrip("/") + "/"
        self._remote = RemoteClientHelper.build(
            base_url=resolved_base_url,
            session=self._session,
            timeout=self.timeout,
        )
        self.base_url = self._remote.base_url
        self._transport = self._remote.transport

        self._graph_catalog_cache: Optional[Dict[str, GraphTarget]] = None
        self._ontology_registry: Dict[str, Any] = {}  # database -> Ontology
        self._indexing_design: Optional[Any] = None

    def _default_reasoning_cycle(self) -> Dict[str, Any]:
        extra = getattr(self.agent_config, "extra", {}) or {}
        value = extra.get("agent_design_reasoning_cycle")
        if isinstance(value, dict):
            return dict(value)
        return {}

    # ------------------------------------------------------------------
    # Convenience factories — shorten the 0→hello-world distance
    # ------------------------------------------------------------------

    @classmethod
    def local(
        cls,
        ontology: Any,
        *,
        llm: str = "openai/gpt-4o",
        graph: Optional[str] = None,
        neo4j_user: str = "neo4j",
        neo4j_password: str = "password",
        api_key: Optional[str] = None,
        **kwargs: Any,
    ) -> "Seocho":
        """Create a local-engine ``Seocho`` with sensible defaults.

        Zero-config path (uses embedded LadybugDB, no server needed)::

            s = Seocho.local(ontology)   # → .seocho/local.lbug
            s.add("text")
            s.ask("question")

        Neo4j/DozerDB path::

            s = Seocho.local(ontology, graph="bolt://localhost:7687")

        Args:
            ontology: :class:`~seocho.ontology.Ontology` to bind.
            llm: Provider/model string (``"openai/gpt-4o"``,
                ``"deepseek/deepseek-chat"``, ``"kimi/kimi-k2.5"``) or plain
                model name (defaults to ``openai``).
            graph: Graph backend selector.
                - ``None`` (default): embedded LadybugDB at ``.seocho/local.lbug``.
                - ``"bolt://..."``: Neo4j/DozerDB over Bolt protocol.
                - Any other path: LadybugDB file path.
            neo4j_user: Neo4j username (only used when *graph* is a Bolt URI).
            neo4j_password: Neo4j password (only used when *graph* is a Bolt URI).
            api_key: Optional API key override for the LLM provider.
                Falls back to the provider's env var (``OPENAI_API_KEY`` etc.).
            **kwargs: Extra arguments forwarded to the :class:`Seocho`
                constructor (``workspace_id``, ``agent_config``,
                ``extraction_prompt``, …).

        Returns:
            A configured :class:`Seocho` in local engine mode.
        """
        from .store.llm import create_llm_backend

        provider, model = (llm.split("/", 1) if "/" in llm else ("openai", llm))
        llm_backend = create_llm_backend(
            provider=provider.strip(),
            model=model.strip(),
            api_key=api_key,
        )

        if graph and graph.startswith(("bolt://", "neo4j://", "neo4j+s://", "bolt+s://")):
            from .store.graph import Neo4jGraphStore
            graph_store = Neo4jGraphStore(graph, neo4j_user, neo4j_password)
        else:
            from .store.graph import LadybugGraphStore
            path = graph or ".seocho/local.lbug"
            graph_store = LadybugGraphStore(path)
            # Declare tables from the ontology so writes work immediately
            try:
                graph_store.ensure_constraints(ontology)
            except Exception:
                pass

        return cls(
            ontology=ontology,
            graph_store=graph_store,
            llm=llm_backend,
            **kwargs,
        )

    @classmethod
    def remote(cls, base_url: str, **kwargs: Any) -> "Seocho":
        """Create an HTTP-client ``Seocho`` pointing at a running runtime.

        Equivalent to ``Seocho(base_url=base_url, **kwargs)`` but makes
        the intent explicit at the call site.
        """
        return cls(base_url=base_url, **kwargs)

    @classmethod
    def from_agent_design(
        cls,
        agent_design: Any,
        *,
        ontology: Optional[Any] = None,
        graph_store: Optional[Any] = None,
        llm: Any = None,
        graph: Optional[str] = None,
        base_url: Optional[str] = None,
        workspace_id: Optional[str] = None,
        neo4j_user: str = "neo4j",
        neo4j_password: str = "password",
        api_key: Optional[str] = None,
        **kwargs: Any,
    ) -> "Seocho":
        """Create a client from a YAML-backed agent design specification.

        ``agent_design`` may be an :class:`~seocho.agent_design.AgentDesignSpec`
        instance or a path to a YAML file. The spec compiles into an
        :class:`~seocho.agent_config.AgentConfig` plus a stable
        ``ontology_profile`` default.

        Remote mode:

        ``Seocho.from_agent_design("design.yaml", base_url="http://localhost:8001")``

        Local mode:

        ``Seocho.from_agent_design("design.yaml", ontology=onto, graph_store=store, llm=llm)``
        """
        from .agent_design import AgentDesignSpec, load_agent_design_spec

        if isinstance(agent_design, AgentDesignSpec):
            spec = agent_design
        else:
            spec = load_agent_design_spec(Path(agent_design))

        client_kwargs = spec.client_kwargs()
        kwargs.setdefault("agent_config", client_kwargs["agent_config"])
        kwargs.setdefault("ontology_profile", client_kwargs["ontology_profile"])

        if base_url:
            return cls(base_url=base_url, workspace_id=workspace_id, **kwargs)

        if ontology is None and spec.ontology.required:
            raise ValueError(
                "Agent design specs with required ontology bindings need an ontology object "
                "when constructing a local Seocho client."
            )
        if ontology is None:
            raise ValueError("Local agent design construction requires an ontology object.")

        if graph_store is not None or llm is not None and not isinstance(llm, str):
            if graph_store is None or llm is None:
                raise ValueError(
                    "Provide both graph_store and llm when constructing a direct local "
                    "Seocho client from an agent design."
                )
            return cls(
                ontology=ontology,
                graph_store=graph_store,
                llm=llm,
                workspace_id=workspace_id,
                **kwargs,
            )

        return cls.local(
            ontology,
            llm=str(llm or "openai/gpt-4o"),
            graph=graph,
            neo4j_user=neo4j_user,
            neo4j_password=neo4j_password,
            api_key=api_key,
            workspace_id=workspace_id,
            **kwargs,
        )

    @classmethod
    def from_indexing_design(
        cls,
        indexing_design: Any,
        *,
        ontology: Optional[Any] = None,
        graph_store: Optional[Any] = None,
        llm: Any = None,
        graph: Optional[str] = None,
        base_url: Optional[str] = None,
        workspace_id: Optional[str] = None,
        neo4j_user: str = "neo4j",
        neo4j_password: str = "password",
        api_key: Optional[str] = None,
        **kwargs: Any,
    ) -> "Seocho":
        """Create a local client from a YAML-backed indexing design specification.

        ``indexing_design`` may be an :class:`~seocho.indexing_design.IndexingDesignSpec`
        instance or a path to a YAML file. The spec materializes an ontology
        graph model (`lpg`, `rdf`, or `hybrid`) and injects stable local
        indexing defaults for metadata and validation behavior.
        """
        from .indexing_design import IndexingDesignSpec, load_indexing_design_spec

        if isinstance(indexing_design, IndexingDesignSpec):
            spec = indexing_design
        else:
            spec = load_indexing_design_spec(Path(indexing_design))

        if base_url:
            raise ValueError(
                "Indexing design specs currently apply to local SDK construction only. "
                "Provide ontology plus a local graph target."
            )
        if ontology is None:
            raise ValueError(
                "Indexing design specs require an ontology object when constructing a local Seocho client."
            )
        if spec.requires_workspace_id() and not str(workspace_id or "").strip():
            raise ValueError(
                "Indexing design specs with constraints.require_workspace_id=true "
                "need a workspace_id."
            )

        client_kwargs = spec.client_kwargs(ontology=ontology)
        kwargs.setdefault("ontology_profile", client_kwargs["ontology_profile"])
        extraction_prompt = client_kwargs.get("extraction_prompt")
        if extraction_prompt is not None:
            kwargs.setdefault("extraction_prompt", extraction_prompt)
        materialized_ontology = client_kwargs["ontology"]

        if graph_store is not None or llm is not None and not isinstance(llm, str):
            if graph_store is None or llm is None:
                raise ValueError(
                    "Provide both graph_store and llm when constructing a direct local "
                    "Seocho client from an indexing design."
                )
            client = cls(
                ontology=materialized_ontology,
                graph_store=graph_store,
                llm=llm,
                workspace_id=workspace_id,
                **kwargs,
            )
        else:
            if spec.storage_target in {"neo4j", "dozerdb"} and not graph:
                raise ValueError(
                    "Indexing design specs targeting Neo4j/DozerDB require graph='bolt://...'"
                    " or an explicit graph_store."
                )
            client = cls.local(
                materialized_ontology,
                llm=str(llm or "openai/gpt-4o"),
                graph=graph,
                neo4j_user=neo4j_user,
                neo4j_password=neo4j_password,
                api_key=api_key,
                workspace_id=workspace_id,
                **kwargs,
            )

        client._indexing_design = spec
        return client

    def _resolve_indexing_design_add_kwargs(
        self,
        *,
        metadata: Optional[Dict[str, Any]],
        strict_validation: bool,
    ) -> Dict[str, Any]:
        if self._indexing_design is None:
            return {
                "metadata": metadata,
                "strict_validation": strict_validation,
            }
        return self._indexing_design.apply_add_defaults(
            metadata=metadata,
            strict_validation=strict_validation,
        )

    def agent(self, kind: str = "indexing", *, name: Optional[str] = None, model: Optional[str] = None) -> Any:
        """Create an agent with this client's ontology, graph_store, and llm pre-wired.

        Only available in local engine mode (requires ``ontology``, ``graph_store``,
        ``llm`` at construction).

        Args:
            kind: ``"indexing"`` (default), ``"query"``, or ``"supervisor"``.
            name: Optional custom agent name.
            model: Optional model override for the agent (defaults to the client's llm).

        Returns:
            An :class:`agents.Agent` instance ready to use with
            :class:`agents.Runner`.

        Raises:
            RuntimeError: If the client is not in local engine mode.
            ValueError: If *kind* is not recognized.

        Example::

            indexing = seocho.agent("indexing")
            query = seocho.agent("query")
        """
        if not self._local_mode or self.ontology is None:
            raise RuntimeError(
                "agent() requires local engine mode. "
                "Initialize Seocho with ontology, graph_store, and llm."
            )

        kind_lower = kind.strip().lower()
        shared_kwargs: Dict[str, Any] = {
            "ontology": self.ontology,
            "graph_store": self.graph_store,
            "llm": self.llm,
        }
        ontology_context = self._engine._ontology_context_cache.get(
            self.ontology,
            workspace_id=self.workspace_id,
            profile=self.ontology_profile,
        )
        shared_kwargs["ontology_context"] = ontology_context
        shared_kwargs["workspace_id"] = self.workspace_id
        if model is not None:
            shared_kwargs["model"] = model
        if name is not None:
            shared_kwargs["name"] = name

        if kind_lower == "indexing":
            from .agent.factory import create_indexing_agent
            return create_indexing_agent(**shared_kwargs)
        if kind_lower == "query":
            from .agent.factory import create_query_agent
            return create_query_agent(**shared_kwargs)
        if kind_lower == "supervisor":
            from .agent.factory import create_supervisor_agent
            return create_supervisor_agent(**shared_kwargs)
        raise ValueError(
            f"Unknown agent kind: {kind!r}. Expected 'indexing', 'query', or 'supervisor'."
        )

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

    def _require_ontology_contract(self, database: Optional[str] = None) -> Any:
        from .client_artifacts import require_ontology_contract

        return require_ontology_contract(self, database)

    def approved_artifacts_from_ontology(
        self,
        *,
        database: Optional[str] = None,
        include_vocabulary: bool = True,
        include_property_terms: bool = True,
    ) -> ApprovedArtifacts:
        """Build a runtime ``ApprovedArtifacts`` payload from the current ontology."""
        return build_approved_artifacts_from_ontology(
            self,
            database=database,
            include_vocabulary=include_vocabulary,
            include_property_terms=include_property_terms,
        )

    def artifact_draft_from_ontology(
        self,
        *,
        database: Optional[str] = None,
        name: Optional[str] = None,
        include_vocabulary: bool = True,
        include_property_terms: bool = True,
        source_summary: Optional[Dict[str, Any]] = None,
    ) -> SemanticArtifactDraftInput:
        """Build a draft semantic artifact payload from the current ontology."""
        return build_artifact_draft_from_ontology(
            self,
            database=database,
            name=name,
            include_vocabulary=include_vocabulary,
            include_property_terms=include_property_terms,
            source_summary=source_summary,
        )

    def promote_artifact(
        self,
        artifact: Any,
        database: str,
        *,
        version: Optional[str] = None,
        apply_constraints: bool = True,
    ) -> Any:
        """Promote a semantic artifact to the active ontology for a database.

        Converts a :class:`~seocho.semantic.SemanticArtifact`,
        :class:`~seocho.semantic.SemanticArtifactDraftInput`, or plain dict
        into an :class:`~seocho.ontology.Ontology` and registers it.

        Args:
            artifact: Approved (or draft) artifact to promote.
            database: Target database to bind the new ontology to.
            version: Ontology version string; inferred from artifact if omitted.
            apply_constraints: Apply the new ontology's constraints to the
                database after promotion (requires local mode).

        Returns:
            The newly created :class:`~seocho.ontology.Ontology` instance.

        Example::

            approved = seocho.get_artifact("art_abc123")
            onto = seocho.promote_artifact(approved, "mydb", version="2.0")
        """
        from .ontology import Ontology as Ont

        new_ontology = Ont.from_artifact(artifact, version=version or "1.0.0")
        self.register_ontology(database, new_ontology)

        if apply_constraints and self._local_mode and self._engine is not None:
            try:
                self._engine.graph_store.ensure_constraints(
                    new_ontology, database=database,
                )
            except Exception as exc:
                logger.warning("promote_artifact: constraint application failed: %s", exc)

        return new_ontology

    def coverage_stats(self, database: Optional[str] = None) -> Dict[str, Any]:
        """Compute ontology coverage statistics for a database.

        Requires local mode (graph_store access). Returns per-node-type
        and per-relationship-type instance counts with an overall coverage
        score (0.0–1.0).

        Args:
            database: Target database; defaults to ``default_database``.

        Returns:
            Dict with ``node_coverage``, ``relationship_coverage``,
            ``overall_score``, and ``unused`` lists.

        Raises:
            RuntimeError: If not in local mode.
        """
        if not self._local_mode or self._engine is None:
            raise RuntimeError(
                "coverage_stats() requires local mode. "
                "Initialize Seocho with a graph_store."
            )
        db = database or self.default_database
        ontology = self.get_ontology(db)
        return ontology.coverage_stats(self._engine.graph_store, database=db)

    def prompt_context_from_ontology(
        self,
        *,
        database: Optional[str] = None,
        instructions: Optional[Sequence[str]] = None,
        include_vocabulary: bool = True,
        include_property_terms: bool = True,
    ) -> SemanticPromptContext:
        """Build a typed semantic prompt context from the current ontology."""
        return build_prompt_context_from_ontology(
            self,
            database=database,
            instructions=instructions,
            include_vocabulary=include_vocabulary,
            include_property_terms=include_property_terms,
        )

    def migrate(
        self,
        database: str,
        new_ontology: Any,
        *,
        dry_run: bool = False,
        apply_constraints: bool = True,
    ) -> Dict[str, Any]:
        """Migrate a database from its current ontology to *new_ontology*.

        Computes a migration plan (label/property/relationship diffs), then
        executes the generated Cypher statements against the target database.
        After a successful migration the ontology registry is updated
        automatically.

        Requires local mode (``_local_mode=True``).

        Args:
            database: Target database to migrate.
            new_ontology: The :class:`~seocho.ontology.Ontology` to migrate to.
            dry_run: If ``True``, return the plan without executing anything.
            apply_constraints: After migration, apply the new ontology's
                constraints/indexes to the database (default ``True``).

        Returns:
            Dict with ``plan``, ``executed``, ``errors``, ``dry_run``,
            and ``constraints`` (if *apply_constraints* is ``True``).

        Raises:
            RuntimeError: If not in local mode (no graph_store available).

        Example::

            result = seocho.migrate("mydb", new_onto)
            print(result["plan"]["summary"])
            # "Migration 1.0 → 2.0: 1 additions, 0 removals, 0 Cypher statements"
        """
        if not self._local_mode or self._engine is None:
            raise RuntimeError(
                "migrate() requires local mode. "
                "Initialize Seocho with a graph_store to use migrations."
            )

        current_ontology = self.get_ontology(database)
        result = current_ontology.apply_migration(
            graph_store=self._engine.graph_store,
            new_ontology=new_ontology,
            database=database,
            dry_run=dry_run,
        )

        if dry_run:
            return result

        if not result["errors"]:
            self.register_ontology(database, new_ontology)
            result["ontology_updated"] = True

            if apply_constraints:
                try:
                    constraint_result = self._engine.graph_store.ensure_constraints(
                        new_ontology, database=database,
                    )
                    result["constraints"] = constraint_result
                except Exception as exc:
                    result["constraints"] = {"success": 0, "errors": [str(exc)]}
        else:
            result["ontology_updated"] = False

        return result

    # ------------------------------------------------------------------
    # Core API — works in both modes
    # ------------------------------------------------------------------

    def add(
        self,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        strict_validation: bool = False,
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
            add_kwargs = self._resolve_indexing_design_add_kwargs(
                metadata=metadata,
                strict_validation=strict_validation,
            )
            return self._engine.add(
                content,
                database=db,
                category=category,
                metadata=add_kwargs["metadata"],
                strict_validation=bool(add_kwargs["strict_validation"]),
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

    @property
    def last_query_metadata(self) -> Dict[str, Any]:
        """Return the latest local query observability payload, if available."""

        if self._engine is None:
            return {}
        metadata = getattr(self._engine, "_last_query_metadata", {})
        return dict(metadata) if isinstance(metadata, dict) else {}

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
        add_kwargs = self._resolve_indexing_design_add_kwargs(
            metadata=metadata,
            strict_validation=strict_validation,
        )
        return self._engine.add_batch(
            documents, database=database, category=category,
            metadata=add_kwargs["metadata"], strict_validation=bool(add_kwargs["strict_validation"]),
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
            ontology_profile=self.ontology_profile,
            user_id=self.user_id,
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
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
        prefer_agentic_tools: bool = False,
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
            max_steps=max_steps,
            tool_budget=tool_budget,
            prefer_agentic_tools=prefer_agentic_tools,
        )
        payload = self._request_json("POST", RuntimePath.RUN_AGENT, json_body=body)
        return AgentRunResponse.from_dict(payload)

    def react(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[str]] = None,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
    ) -> AgentRunResponse:
        """Run the graph-scoped tool-using router path."""
        return self.router(
            query,
            user_id=user_id,
            graph_ids=graph_ids,
            max_steps=max_steps,
            tool_budget=tool_budget,
            prefer_agentic_tools=True,
        )

    def advanced(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        graph_ids: Optional[Sequence[GraphRef | GraphTarget | Dict[str, Any] | str]] = None,
        reasoning_cycle: Optional[Dict[str, Any]] = None,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
    ) -> DebateRunResponse:
        """Run the explicit advanced multi-agent debate path."""
        return self.debate(
            query,
            user_id=user_id,
            graph_ids=graph_ids,
            reasoning_cycle=reasoning_cycle,
            max_steps=max_steps,
            tool_budget=tool_budget,
        )

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
        reasoning_cycle: Optional[Dict[str, Any]] = None,
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
        effective_reasoning_cycle = dict(reasoning_cycle or self._default_reasoning_cycle())
        body = build_query_payload(
            query=query,
            workspace_id=self.workspace_id,
            default_user_id=self.user_id,
            user_id=user_id,
            graph_ids=resolved_graph_ids,
            reasoning_cycle=effective_reasoning_cycle or None,
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
        reasoning_cycle: Optional[Dict[str, Any]] = None,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
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
        effective_reasoning_cycle = dict(reasoning_cycle or self._default_reasoning_cycle())
        body = build_query_payload(
            query=query,
            workspace_id=self.workspace_id,
            default_user_id=self.user_id,
            user_id=user_id,
            graph_ids=resolved_graph_ids,
            reasoning_cycle=effective_reasoning_cycle or None,
            max_steps=max_steps,
            tool_budget=tool_budget,
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
                reasoning_cycle=resolved_plan.reasoning.reasoning_cycle or None,
                max_steps=resolved_plan.reasoning.max_steps,
                tool_budget=resolved_plan.reasoning.tool_budget,
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
                max_steps=resolved_plan.reasoning.max_steps,
                tool_budget=resolved_plan.reasoning.tool_budget,
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
            reasoning_cycle=resolved_plan.reasoning.reasoning_cycle or None,
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
        reasoning_cycle: Optional[Dict[str, Any]] = None,
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
        effective_reasoning_cycle = dict(reasoning_cycle or self._default_reasoning_cycle())
        if effective_reasoning_cycle:
            body["reasoning_cycle"] = effective_reasoning_cycle
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
        return self._bundle_helper.export_bundle(
            self,
            path=path,
            app_name=app_name,
            default_database=default_database,
        )

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
        return RuntimeBundleClientHelper.create_client(bundle_source, workspace_id=workspace_id)

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
        databases = plan.databases or ([plan.graph_ids[0]] if plan.graph_ids else [])
        database = databases[0] if databases else "neo4j"
        response = self.ask(plan.query, database=database, user_id=plan.user_id)
        metadata = getattr(self._engine, "_last_query_metadata", {}) if self._engine is not None else {}
        return ExecutionResult(
            requested_style="direct",
            runtime_mode="semantic",
            response=response,
            resolved_targets=plan.targets,
            graph_ids=plan.graph_ids,
            databases=[database] if database else [],
            trace_steps=[],
            ontology_context_mismatch=dict(metadata.get("ontology_context_mismatch", {})),
            answer_envelope=dict(metadata.get("answer_envelope", {})),
            latency_breakdown_ms=dict(metadata.get("latency_breakdown_ms", {})),
            agent_pattern=dict(metadata.get("agent_pattern", {})),
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
        return self._remote.request_json(
            method,
            path,
            json_body=json_body,
            params=params,
        )


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
        """Initialize the builder with a client, query, and optional scope."""
        self._client = client
        self._query = query
        self._targets: List[GraphRef] = []
        self._reasoning = ReasoningPolicy(
            reasoning_cycle=self._client._default_reasoning_cycle(),
        )
        self._entity_overrides: List[EntityOverride] = []
        self._ontology_ids: List[str] = []
        self._vocabulary_profiles: List[str] = []
        self._user_id = user_id
        self._session_id = session_id

    def on_graph(self, graph: GraphRef | GraphTarget | Dict[str, Any] | str) -> "ExecutionPlanBuilder":
        """Target a single graph for execution."""
        return self.on_graphs(graph)

    def on_graphs(self, *graphs: GraphRef | GraphTarget | Dict[str, Any] | str) -> "ExecutionPlanBuilder":
        """Target one or more graphs for execution."""
        for graph in graphs:
            if isinstance(graph, (list, tuple)):
                for item in graph:
                    self._targets.append(self._client._coerce_graph_ref(item))
                continue
            self._targets.append(self._client._coerce_graph_ref(graph))
        return self

    def with_ontology(self, *ontology_ids: str) -> "ExecutionPlanBuilder":
        """Filter target graphs by ontology ID."""
        self._ontology_ids = [str(item).strip() for item in ontology_ids if str(item).strip()]
        return self

    def with_vocabulary(self, *vocabulary_profiles: str) -> "ExecutionPlanBuilder":
        """Filter target graphs by vocabulary profile."""
        self._vocabulary_profiles = [
            str(item).strip() for item in vocabulary_profiles if str(item).strip()
        ]
        return self

    def with_reasoning_cycle(
        self,
        reasoning_cycle: Dict[str, Any],
    ) -> "ExecutionPlanBuilder":
        """Attach an anomaly-driven inquiry contract to semantic/debate execution."""
        self._reasoning = ReasoningPolicy(
            style=self._reasoning.normalized_style(),
            max_steps=self._reasoning.max_steps,
            tool_budget=self._reasoning.tool_budget,
            require_grounded_evidence=self._reasoning.require_grounded_evidence,
            repair_budget=self._reasoning.repair_budget,
            fallback_style=self._reasoning.fallback_style,
            reasoning_cycle=dict(reasoning_cycle or {}),
        )
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
        """Set the reasoning policy for query execution.

        Args:
            style: Reasoning style (``"direct"``, ``"react"``, or ``"debate"``).
            max_steps: Maximum reasoning steps for react/debate.
            tool_budget: Maximum tool calls allowed.
            require_grounded_evidence: Require graph-grounded evidence in the answer.
            repair_budget: Maximum query repair attempts on empty results.
            fallback_style: Style to fall back to if the primary style fails.
        """
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
            reasoning_cycle=dict(self._reasoning.reasoning_cycle),
        )
        self._reasoning.normalized_style()
        return self

    def direct(self) -> "ExecutionPlanBuilder":
        """Use direct (single-pass) reasoning style."""
        return self.with_reasoning(style="direct")

    def react(
        self,
        *,
        max_steps: Optional[int] = None,
        tool_budget: Optional[int] = None,
        require_grounded_evidence: bool = True,
        fallback_style: Optional[str] = None,
    ) -> "ExecutionPlanBuilder":
        """Use the runtime's react-style reasoning policy."""
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
        """Use multi-agent debate reasoning style."""
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
        """Alias for :meth:`debate` reasoning style."""
        return self.debate(
            max_steps=max_steps,
            tool_budget=tool_budget,
            require_grounded_evidence=require_grounded_evidence,
            fallback_style=fallback_style,
        )

    def with_repair_budget(self, repair_budget: int) -> "ExecutionPlanBuilder":
        """Set the maximum number of query repair attempts on empty results."""
        self._reasoning = ReasoningPolicy(
            style=self._reasoning.normalized_style(),
            max_steps=self._reasoning.max_steps,
            tool_budget=self._reasoning.tool_budget,
            require_grounded_evidence=self._reasoning.require_grounded_evidence,
            repair_budget=max(0, int(repair_budget)),
            fallback_style=self._reasoning.fallback_style,
            reasoning_cycle=dict(self._reasoning.reasoning_cycle),
        )
        return self

    def with_entity_overrides(
        self,
        *entity_overrides: EntityOverride | Dict[str, Any],
    ) -> "ExecutionPlanBuilder":
        """Provide entity disambiguation overrides for the query."""
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
        """Set the user ID for this execution plan."""
        self._user_id = user_id
        return self

    def in_session(self, session_id: str) -> "ExecutionPlanBuilder":
        """Set the session ID for this execution plan."""
        self._session_id = session_id
        return self

    def build(self) -> ExecutionPlan:
        """Build the execution plan without running it.

        Returns:
            An :class:`ExecutionPlan` ready for :meth:`Seocho.execute`.
        """
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
        """Build and execute the plan in one step.

        Returns:
            An :class:`ExecutionResult` with the answer and execution metadata.
        """
        return self._client.execute(self.build())


# ======================================================================
# Local Engine — orchestrates ontology + LLM + graph store in-process
# ======================================================================


# ======================================================================
# AsyncSeocho
# ======================================================================


class AsyncSeocho:
    """Async wrapper around the sync client for notebook and app usage."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the async client. Accepts the same arguments as :class:`Seocho`."""
        self._client = Seocho(**kwargs)

    async def add(self, content: str, **kwargs: Any) -> Memory:
        """Async version of :meth:`Seocho.add`."""
        return await asyncio.to_thread(self._client.add, content, **kwargs)

    async def add_with_details(self, content: str, **kwargs: Any) -> MemoryCreateResult:
        """Async version of :meth:`Seocho.add_with_details`."""
        return await asyncio.to_thread(self._client.add_with_details, content, **kwargs)

    async def apply_artifact(self, artifact_id: str, content: str, **kwargs: Any) -> MemoryCreateResult:
        """Async version of :meth:`Seocho.apply_artifact`."""
        return await asyncio.to_thread(self._client.apply_artifact, artifact_id, content, **kwargs)

    async def get(self, memory_id: str, **kwargs: Any) -> Memory:
        """Async version of :meth:`Seocho.get`."""
        return await asyncio.to_thread(self._client.get, memory_id, **kwargs)

    async def search(self, query: str, **kwargs: Any) -> List[SearchResult]:
        """Async version of :meth:`Seocho.search`."""
        return await asyncio.to_thread(self._client.search, query, **kwargs)

    async def search_with_context(self, query: str, **kwargs: Any) -> SearchResponse:
        """Async version of :meth:`Seocho.search_with_context`."""
        return await asyncio.to_thread(self._client.search_with_context, query, **kwargs)

    async def ask(self, message: str, **kwargs: Any) -> str:
        """Async version of :meth:`Seocho.ask`."""
        return await asyncio.to_thread(self._client.ask, message, **kwargs)

    async def chat(self, message: str, **kwargs: Any) -> ChatResponse:
        """Async version of :meth:`Seocho.chat`."""
        return await asyncio.to_thread(self._client.chat, message, **kwargs)

    async def delete(self, memory_id: str, **kwargs: Any) -> ArchiveResult:
        """Async version of :meth:`Seocho.delete`."""
        return await asyncio.to_thread(self._client.delete, memory_id, **kwargs)

    async def extract(self, content: str, **kwargs: Any) -> Dict[str, Any]:
        """Async version of :meth:`Seocho.extract`."""
        return await asyncio.to_thread(self._client.extract, content, **kwargs)

    async def query(self, cypher: str, **kwargs: Any) -> List[Dict[str, Any]]:
        """Async version of :meth:`Seocho.query`."""
        return await asyncio.to_thread(self._client.query, cypher, **kwargs)

    async def router(self, query: str, **kwargs: Any) -> AgentRunResponse:
        """Async version of :meth:`Seocho.router`."""
        return await asyncio.to_thread(self._client.router, query, **kwargs)

    async def react(self, query: str, **kwargs: Any) -> AgentRunResponse:
        """Async version of :meth:`Seocho.react`."""
        return await asyncio.to_thread(self._client.react, query, **kwargs)

    async def advanced(self, query: str, **kwargs: Any) -> DebateRunResponse:
        """Async version of :meth:`Seocho.advanced`."""
        return await asyncio.to_thread(self._client.advanced, query, **kwargs)

    async def semantic(self, query: str, **kwargs: Any) -> SemanticRunResponse:
        """Async version of :meth:`Seocho.semantic`."""
        return await asyncio.to_thread(self._client.semantic, query, **kwargs)

    async def debate(self, query: str, **kwargs: Any) -> DebateRunResponse:
        """Async version of :meth:`Seocho.debate`."""
        return await asyncio.to_thread(self._client.debate, query, **kwargs)

    async def execute(self, plan: ExecutionPlan | Dict[str, Any]) -> ExecutionResult:
        """Async version of :meth:`Seocho.execute`."""
        return await asyncio.to_thread(self._client.execute, plan)

    async def platform_chat(self, message: str, **kwargs: Any) -> PlatformChatResponse:
        """Async version of :meth:`Seocho.platform_chat`."""
        return await asyncio.to_thread(self._client.platform_chat, message, **kwargs)

    async def session_history(self, session_id: str) -> PlatformSessionResponse:
        """Async version of :meth:`Seocho.session_history`."""
        return await asyncio.to_thread(self._client.session_history, session_id)

    async def reset_session(self, session_id: str) -> PlatformSessionResponse:
        """Async version of :meth:`Seocho.reset_session`."""
        return await asyncio.to_thread(self._client.reset_session, session_id)

    async def raw_ingest(self, records: Sequence[Dict[str, Any]], **kwargs: Any) -> RawIngestResult:
        """Async version of :meth:`Seocho.raw_ingest`."""
        return await asyncio.to_thread(self._client.raw_ingest, records, **kwargs)

    async def graphs(self) -> List[GraphTarget]:
        """Async version of :meth:`Seocho.graphs`."""
        return await asyncio.to_thread(self._client.graphs)

    async def databases(self) -> List[str]:
        """Async version of :meth:`Seocho.databases`."""
        return await asyncio.to_thread(self._client.databases)

    async def agents(self) -> List[str]:
        """Async version of :meth:`Seocho.agents`."""
        return await asyncio.to_thread(self._client.agents)

    async def health(self, *, scope: str = "runtime") -> Dict[str, Any]:
        """Async version of :meth:`Seocho.health`."""
        return await asyncio.to_thread(self._client.health, scope=scope)

    async def semantic_runs(
        self,
        *,
        limit: int = 20,
        route: Optional[str] = None,
        intent_id: Optional[str] = None,
    ) -> List[SemanticRunRecord]:
        """Async version of :meth:`Seocho.semantic_runs`."""
        return await asyncio.to_thread(
            self._client.semantic_runs,
            limit=limit,
            route=route,
            intent_id=intent_id,
        )

    async def semantic_run(self, run_id: str) -> SemanticRunRecord:
        """Async version of :meth:`Seocho.semantic_run`."""
        return await asyncio.to_thread(self._client.semantic_run, run_id)

    async def ensure_fulltext_indexes(self, **kwargs: Any) -> FulltextIndexResponse:
        """Async version of :meth:`Seocho.ensure_fulltext_indexes`."""
        return await asyncio.to_thread(self._client.ensure_fulltext_indexes, **kwargs)

    async def list_artifacts(self, *, status: Optional[str] = None) -> List[SemanticArtifactSummary]:
        """Async version of :meth:`Seocho.list_artifacts`."""
        return await asyncio.to_thread(self._client.list_artifacts, status=status)

    async def get_artifact(self, artifact_id: str) -> SemanticArtifact:
        """Async version of :meth:`Seocho.get_artifact`."""
        return await asyncio.to_thread(self._client.get_artifact, artifact_id)

    async def create_artifact_draft(
        self,
        draft: SemanticArtifactDraftInput | Dict[str, Any],
    ) -> SemanticArtifact:
        """Async version of :meth:`Seocho.create_artifact_draft`."""
        return await asyncio.to_thread(self._client.create_artifact_draft, draft)

    async def approve_artifact(
        self,
        artifact_id: str,
        *,
        approved_by: str,
        approval_note: Optional[str] = None,
    ) -> SemanticArtifact:
        """Async version of :meth:`Seocho.approve_artifact`."""
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
        """Async version of :meth:`Seocho.deprecate_artifact`."""
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
        """Async version of :meth:`Seocho.validate_artifact`."""
        return await asyncio.to_thread(self._client.validate_artifact, artifact)

    async def diff_artifacts(
        self,
        left: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
        right: SemanticArtifact | SemanticArtifactDraftInput | Dict[str, Any],
    ) -> ArtifactDiff:
        """Async version of :meth:`Seocho.diff_artifacts`."""
        return await asyncio.to_thread(self._client.diff_artifacts, left, right)

    async def migrate(
        self,
        database: str,
        new_ontology: Any,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Async version of :meth:`Seocho.migrate`."""
        return await asyncio.to_thread(
            self._client.migrate, database, new_ontology, **kwargs,
        )

    async def approved_artifacts_from_ontology(
        self,
        *,
        database: Optional[str] = None,
        include_vocabulary: bool = True,
        include_property_terms: bool = True,
    ) -> ApprovedArtifacts:
        """Async version of :meth:`Seocho.approved_artifacts_from_ontology`."""
        return await asyncio.to_thread(
            self._client.approved_artifacts_from_ontology,
            database=database,
            include_vocabulary=include_vocabulary,
            include_property_terms=include_property_terms,
        )

    async def artifact_draft_from_ontology(
        self,
        *,
        database: Optional[str] = None,
        name: Optional[str] = None,
        include_vocabulary: bool = True,
        include_property_terms: bool = True,
        source_summary: Optional[Dict[str, Any]] = None,
    ) -> SemanticArtifactDraftInput:
        """Async version of :meth:`Seocho.artifact_draft_from_ontology`."""
        return await asyncio.to_thread(
            self._client.artifact_draft_from_ontology,
            database=database,
            name=name,
            include_vocabulary=include_vocabulary,
            include_property_terms=include_property_terms,
            source_summary=source_summary,
        )

    async def prompt_context_from_ontology(
        self,
        *,
        database: Optional[str] = None,
        instructions: Optional[Sequence[str]] = None,
        include_vocabulary: bool = True,
        include_property_terms: bool = True,
    ) -> SemanticPromptContext:
        """Async version of :meth:`Seocho.prompt_context_from_ontology`."""
        return await asyncio.to_thread(
            self._client.prompt_context_from_ontology,
            database=database,
            instructions=instructions,
            include_vocabulary=include_vocabulary,
            include_property_terms=include_property_terms,
        )

    async def aclose(self) -> None:
        """Async version of :meth:`Seocho.close`."""
        await asyncio.to_thread(self._client.close)
