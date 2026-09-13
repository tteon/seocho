"""Thread-offloaded asynchronous SDK facade with preserved public delegates."""

from __future__ import annotations
import asyncio
from typing import Any, Dict, List, Optional, Sequence
from .client_namespaces import (
    IndexNamespace,
    OntologyNamespace,
    PlatformNamespace,
    SessionNamespace,
)
from .governance import ArtifactDiff, ArtifactValidationResult
from .ontology_control_plane import (
    CompiledOntologyProfile,
    OntologyProfile,
    OntologyProfileEvaluation,
    OntologyProfileSelection,
    OntologySignal,
)
from .semantic import (
    ApprovedArtifacts,
    SemanticArtifact,
    SemanticArtifactDraftInput,
    SemanticArtifactSummary,
    SemanticPromptContext,
)
from .models import (
    AgentRunResponse,
    ArchiveResult,
    AskResponse,
    ChatResponse,
    DebateRunResponse,
    ExecutionPlan,
    ExecutionResult,
    FulltextIndexResponse,
    GraphTarget,
    Memory,
    MemoryCreateResult,
    PlatformChatResponse,
    PlatformSessionResponse,
    RawIngestResult,
    SearchResponse,
    SearchResult,
    SemanticRunRecord,
    SemanticRunResponse,
)
from .qualification import (
    CurationDecisionResult,
    CurationPreview,
    GraphProjectionResult,
    QualificationCase,
    QualificationRunResult,
)


class AsyncSeocho:
    """Async wrapper around the sync client for notebook and app usage.

    Methods written out below are hand-authored. Everything else on
    :class:`Seocho` is generated at class creation as a `to_thread` delegate by
    :func:`_fill_async_surface`, so the two surfaces cannot drift.

    Before that generator this class had 56 methods to `Seocho`'s 80, and the
    25 absent ones — `index_file`, `index_directory`, `reindex`, `plan`,
    `agent`, `build_agent`, `session`, `execute_query`, `close`, … — had no
    declared reason for being absent. 55 pairs were kept identical by hand
    (`seocho-6yf`).
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the async client. Accepts the same arguments as :class:`Seocho`."""
        from .client import Seocho

        self._client = Seocho(**kwargs)

    # The same grouped views as the sync client. `_Namespace` resolves through
    # `getattr(owner, ...)`, so binding to the async client yields the async
    # delegates -- `await sc.index.file(...)` -- with no second mapping table.

    @property
    def index(self) -> "IndexNamespace":
        """Writing into the graph. Members are coroutines."""
        return IndexNamespace(self)

    @property
    def governance(self) -> "OntologyNamespace":
        """Operations ON the ontology — artifacts, profiles, signals, curation.

        Named `governance` rather than `ontology` because `self.ontology` is
        already the registered `Ontology` object. The collision forced a better
        split than the one originally planned: the noun stays the thing, the
        namespace is what you do to it.
        """
        return OntologyNamespace(self)

    @property
    def platform(self) -> "PlatformNamespace":
        """Deployment-shell surface."""
        return PlatformNamespace(self)

    @property
    def sessions(self) -> "SessionNamespace":
        """Platform session state."""
        return SessionNamespace(self)

    async def add(self, content: str, **kwargs: Any) -> Memory:
        """Async version of :meth:`Seocho.add`."""
        return await asyncio.to_thread(self._client.add, content, **kwargs)

    async def add_graph(self, graph_data: Dict[str, Any], **kwargs: Any) -> Memory:
        """Async version of :meth:`Seocho.add_graph`."""
        return await asyncio.to_thread(self._client.add_graph, graph_data, **kwargs)

    async def qualify_graph(self, **kwargs: Any) -> QualificationRunResult:
        """Async version of :meth:`Seocho.qualify_graph`."""
        return await asyncio.to_thread(self._client.qualify_graph, **kwargs)

    async def list_curation_cases(self, **kwargs: Any) -> List[QualificationCase]:
        """Async version of :meth:`Seocho.list_curation_cases`."""
        return await asyncio.to_thread(self._client.list_curation_cases, **kwargs)

    async def preview_curation_decision(
        self, case_id: str, **kwargs: Any
    ) -> CurationPreview:
        """Async version of :meth:`Seocho.preview_curation_decision`."""
        return await asyncio.to_thread(
            self._client.preview_curation_decision, case_id, **kwargs
        )

    async def apply_curation_decision(
        self, case_id: str, **kwargs: Any
    ) -> CurationDecisionResult:
        """Async version of :meth:`Seocho.apply_curation_decision`."""
        return await asyncio.to_thread(
            self._client.apply_curation_decision, case_id, **kwargs
        )

    async def project_canonical_graph(self, **kwargs: Any) -> GraphProjectionResult:
        """Async version of :meth:`Seocho.project_canonical_graph`."""
        return await asyncio.to_thread(self._client.project_canonical_graph, **kwargs)

    async def add_with_details(self, content: str, **kwargs: Any) -> MemoryCreateResult:
        """Async version of :meth:`Seocho.add_with_details`."""
        return await asyncio.to_thread(self._client.add_with_details, content, **kwargs)

    async def apply_artifact(
        self, artifact_id: str, content: str, **kwargs: Any
    ) -> MemoryCreateResult:
        """Async version of :meth:`Seocho.apply_artifact`."""
        return await asyncio.to_thread(
            self._client.apply_artifact, artifact_id, content, **kwargs
        )

    async def get(self, memory_id: str, **kwargs: Any) -> Memory:
        """Async version of :meth:`Seocho.get`."""
        return await asyncio.to_thread(self._client.get, memory_id, **kwargs)

    async def search(self, query: str, **kwargs: Any) -> List[SearchResult]:
        """Async version of :meth:`Seocho.search`."""
        return await asyncio.to_thread(self._client.search, query, **kwargs)

    async def search_with_context(self, query: str, **kwargs: Any) -> SearchResponse:
        """Async version of :meth:`Seocho.search_with_context`."""
        return await asyncio.to_thread(
            self._client.search_with_context, query, **kwargs
        )

    async def ask(self, message: str, **kwargs: Any) -> str:
        """Async version of :meth:`Seocho.ask`."""
        return await asyncio.to_thread(self._client.ask, message, **kwargs)

    async def ask_response(self, message: str, **kwargs: Any) -> AskResponse:
        """Async version of :meth:`Seocho.ask_response`."""
        return await asyncio.to_thread(self._client.ask_response, message, **kwargs)

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

    async def session_history(
        self, session_id: str, *, user_id: Optional[str] = None
    ) -> PlatformSessionResponse:
        """Async version of :meth:`Seocho.session_history`."""
        return await asyncio.to_thread(
            self._client.session_history, session_id, user_id=user_id
        )

    async def reset_session(
        self, session_id: str, *, user_id: Optional[str] = None
    ) -> PlatformSessionResponse:
        """Async version of :meth:`Seocho.reset_session`."""
        return await asyncio.to_thread(
            self._client.reset_session, session_id, user_id=user_id
        )

    async def raw_ingest(
        self, records: Sequence[Dict[str, Any]], **kwargs: Any
    ) -> RawIngestResult:
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

    async def list_artifacts(
        self, *, status: Optional[str] = None
    ) -> List[SemanticArtifactSummary]:
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

    async def upsert_ontology_profile(
        self, profile: OntologyProfile | Dict[str, Any]
    ) -> OntologyProfile:
        """Async version of :meth:`Seocho.upsert_ontology_profile`."""
        return await asyncio.to_thread(self._client.upsert_ontology_profile, profile)

    async def list_ontology_profiles(
        self, *, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Async version of :meth:`Seocho.list_ontology_profiles`."""
        return await asyncio.to_thread(
            self._client.list_ontology_profiles, status=status
        )

    async def get_ontology_profile(self, profile_id: str) -> OntologyProfile:
        """Async version of :meth:`Seocho.get_ontology_profile`."""
        return await asyncio.to_thread(self._client.get_ontology_profile, profile_id)

    async def promote_ontology_profile(
        self,
        profile_id: str,
        *,
        promoted_by: str,
        promotion_note: Optional[str] = None,
    ) -> OntologyProfile:
        """Async version of :meth:`Seocho.promote_ontology_profile`."""
        return await asyncio.to_thread(
            self._client.promote_ontology_profile,
            profile_id,
            promoted_by=promoted_by,
            promotion_note=promotion_note,
        )

    async def compile_ontology_profile(
        self, profile_id: str
    ) -> CompiledOntologyProfile:
        """Async version of :meth:`Seocho.compile_ontology_profile`."""
        return await asyncio.to_thread(
            self._client.compile_ontology_profile, profile_id
        )

    async def select_ontology_profile(
        self,
        question: str,
        *,
        route_profile: Optional[Dict[str, Any]] = None,
        include_drafts: bool = False,
    ) -> OntologyProfileSelection:
        """Async version of :meth:`Seocho.select_ontology_profile`."""
        return await asyncio.to_thread(
            self._client.select_ontology_profile,
            question,
            route_profile=route_profile,
            include_drafts=include_drafts,
        )

    async def evaluate_ontology_profile(
        self,
        profile_id: str,
        *,
        baseline_profile_id: Optional[str] = None,
    ) -> OntologyProfileEvaluation:
        """Async version of :meth:`Seocho.evaluate_ontology_profile`."""
        return await asyncio.to_thread(
            self._client.evaluate_ontology_profile,
            profile_id,
            baseline_profile_id=baseline_profile_id,
        )

    async def create_ontology_signal(
        self, signal: OntologySignal | Dict[str, Any]
    ) -> OntologySignal:
        """Async version of :meth:`Seocho.create_ontology_signal`."""
        return await asyncio.to_thread(self._client.create_ontology_signal, signal)

    async def list_ontology_signals(
        self,
        *,
        source: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Async version of :meth:`Seocho.list_ontology_signals`."""
        return await asyncio.to_thread(
            self._client.list_ontology_signals, source=source, kind=kind
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
            self._client.migrate,
            database,
            new_ontology,
            **kwargs,
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


def _fill_async_surface() -> None:
    """Give `AsyncSeocho` a `to_thread` delegate for every un-overridden method.

    Only fills gaps: a name already defined on `AsyncSeocho` is left alone, so
    the hand-written coroutines above — and any that genuinely need different
    async behaviour rather than thread offload — keep winning.

    Deliberately skipped:

    * constructors (`local`, `remote`, `from_*`) — they build a `Seocho`, and an
      async factory returning a sync client would be a lie about the object.
    * `close` — offloading teardown to a worker thread while the event loop may
      still hold references is worse than an explicit sync call.
    * properties — `last_query_metadata` reads state, so awaiting it would make
      a field look like an operation.
    """
    from .client import Seocho

    import functools
    import inspect

    skip = {
        "local",
        "remote",
        "from_agent_design",
        "from_indexing_design",
        "from_runtime_bundle",
        "close",
    }

    def _delegate(method_name: str):
        sync_method = getattr(Seocho, method_name)

        @functools.wraps(sync_method)
        async def _async(self: "AsyncSeocho", *args: Any, **kwargs: Any) -> Any:
            return await asyncio.to_thread(
                getattr(self._client, method_name), *args, **kwargs
            )

        _async.__doc__ = (
            f"Async version of :meth:`Seocho.{method_name}` "
            f"(generated `to_thread` delegate)."
        )
        return _async

    for name, attr in vars(Seocho).items():
        if name.startswith("_") or name in skip:
            continue
        if name in vars(AsyncSeocho):
            continue
        if isinstance(attr, (property, classmethod, staticmethod)):
            continue
        if not inspect.isfunction(attr):
            continue
        setattr(AsyncSeocho, name, _delegate(name))


_fill_async_surface()
