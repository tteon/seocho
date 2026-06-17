"""SEOCHO — Ontology-aligned middleware between agents and graph databases.

Public Plugin Surface (stable contract)
=======================================

Extension is supported through exactly **four** abstract interfaces. New
backends or providers plug in here. Anything else is an internal detail
and must not be treated as an extension point.

1. :class:`seocho.store.graph.GraphStore`
   Graph database backend. Ships with :class:`Neo4jGraphStore` (production)
   and :class:`LadybugGraphStore` (embedded, zero-config).

2. :class:`seocho.store.vector.VectorStore`
   Vector similarity store. Ships with :class:`FAISSVectorStore` and
   :class:`LanceDBVectorStore`.

3. :class:`seocho.store.llm.LLMBackend`
   LLM chat-completion provider. Ships with :class:`OpenAIBackend`,
   :class:`DeepSeekBackend`, :class:`KimiBackend`, :class:`GrokBackend`,
   :class:`QwenBackend`,
   and the base :class:`OpenAICompatibleBackend`.

4. :class:`seocho.store.llm.EmbeddingBackend`
   Embedding provider. Ships with
   :class:`OpenAICompatibleEmbeddingBackend`.

Anything outside these four is **not** an extension point. We deliberately
keep the plugin surface narrow so the ontology alignment contract stays
testable and stable across versions.
"""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from types import ModuleType
from typing import Dict, Iterable, Mapping

try:
    __version__ = version("seocho")
except PackageNotFoundError:
    __version__ = "0.1.0"

_MODULE_EXPORTS: Dict[str, Iterable[str]] = {
    ".api": [
        "advanced",
        "add",
        "add_graph",
        "add_with_details",
        "agents",
        "apply_curation_decision",
        "apply_artifact",
        "ask",
        "ask_response",
        "chat",
        "close",
        "configure",
        "connect",
        "databases",
        "debate",
        "delete",
        "ensure_fulltext_indexes",
        "execute",
        "get",
        "get_client",
        "graphs",
        "health",
        "list_curation_cases",
        "plan",
        "platform_chat",
        "preview_curation_decision",
        "project_canonical_graph",
        "qualify_graph",
        "raw_ingest",
        "react",
        "reset_session",
        "router",
        "search",
        "semantic",
        "semantic_run",
        "semantic_runs",
        "session_history",
    ],
    ".agent_config": [
        "AGENT_PRESETS",
        "AgentConfig",
        "EnsembleExtractionStrategy",
        "IndexingStrategy",
        "ParallelExtractionStrategy",
        "QueryAgentStrategy",
        "RoutingPolicy",
    ],
    ".agent_design": [
        "AgentDesignSpec",
        "OntologyBinding",
        "load_agent_design_spec",
    ],
    ".indexing_design": [
        "IndexingDesignSpec",
        "IndexingOntologyBinding",
        "build_reasoning_cycle_report",
        "load_indexing_design_spec",
    ],
    ".client": [
        "AsyncSeocho",
        "ExecutionPlanBuilder",
        "Seocho",
    ],
    ".curation_design": [
        "CurationDesignSpec",
        "EntityCurationPolicy",
        "PromotionRules",
        "RelationshipCurationPolicy",
        "load_curation_design_spec",
    ],
    ".e2e": [
        "run_from_config",
        "run_sweep_from_config",
    ],
    ".http_runtime": [
        "create_bundle_runtime_app",
    ],
    ".evaluation": [
        "EvaluationBaselineResult",
        "EvaluationCaseResult",
        "EvaluationMatrixSummary",
        "ManualGoldCase",
        "SemanticEvaluationHarness",
    ],
    ".exceptions": [
        "SeochoConnectionError",
        "SeochoError",
        "SeochoHTTPError",
    ],
    ".governance": [
        "ArtifactDiff",
        "ArtifactValidationMessage",
        "ArtifactValidationResult",
    ],
    ".index.enforcement": [
        "EnforcementPolicy",
    ],
    ".indexing": [
        "BatchIndexingResult",
        "IndexingResult",
    ],
    ".local": [
        "LocalRuntimeStatus",
    ],
    ".llm_backend": [
        "DeepSeekBackend",
        "EmbeddingBackend",
        "GrokBackend",
        "KimiBackend",
        "LLMBackend",
        "LLMResponse",
        "OpenAIBackend",
        "OpenAICompatibleBackend",
        "OpenAICompatibleEmbeddingBackend",
        "ProviderSpec",
        "QwenBackend",
        "create_embedding_backend",
        "create_llm_backend",
        "get_provider_spec",
        "list_provider_specs",
    ],
    ".ontology": [
        "Cardinality",
        "NodeDef",
        "NodeDefinition",
        "Ontology",
        "P",
        "Property",
        "PropertyType",
        "RelDef",
        "RelationshipDefinition",
    ],
    ".ontology_context": [
        "CompiledOntologyContext",
        "OntologyContextCache",
        "OntologyContextDescriptor",
        "OntologyDriftError",
        "apply_ontology_context_to_graph_payload",
        "assess_graph_ontology_context_status",
        "assess_ontology_context_mismatch",
        "compile_ontology_context",
        "enforce_drift_policy",
        "ontology_context_graph_properties",
        "query_ontology_context_mismatch",
    ],
    ".run_template": [
        "SweepSpec",
        "load_sweep_spec",
        "load_templated_run_spec",
        "render_run_template",
    ],
    ".run_spec": [
        "RunSpec",
        "RunSpecError",
        "load_run_spec",
    ],
    ".ontology_versioning": [
        "OntologyUpgradePlan",
        "OntologyVersionIdentity",
        "build_ontology_upgrade_plan",
        "is_valid_semver",
        "ontology_schema_fingerprint",
        "ontology_version_identity",
        "parse_semver",
    ],
    ".ontology_control_plane": [
        "CompiledOntologyProfile",
        "OntologyControlPlane",
        "OntologyProfile",
        "OntologyProfileEvaluation",
        "OntologyProfileRegistry",
        "OntologyProfileSelection",
        "OntologySignal",
    ],
    ".ontology_run_context": [
        "OntologyEvidenceState",
        "OntologyPolicyDecision",
        "OntologyRunContext",
        "build_local_ontology_run_context",
        "build_runtime_ontology_run_context",
    ],
    ".session": [
        "Session",
    ],
    ".tracing": [
        "SessionTrace",
        "begin_session",
        "capture_text",
        "configure_tracing_from_env",
        "content_capture_enabled",
        "current_backend_names",
        "disable_tracing",
        "enable_tracing",
        "flush_tracing",
        "is_backend_enabled",
        "is_tracing_enabled",
        "start_span",
    ],
    ".runtime_bundle": [
        "PortablePromptTemplate",
        "RuntimeBundle",
        "RuntimeGraphBinding",
        "RuntimeGraphStoreConfig",
        "RuntimeLLMConfig",
        "build_runtime_bundle",
        "create_client_from_runtime_bundle",
    ],
    ".vector_store": [
        "FAISSVectorStore",
        "LanceDBVectorStore",
        "VectorSearchResult",
        "VectorStore",
        "create_vector_store",
    ],
    ".semantic": [
        "ApprovedArtifacts",
        "KnownEntity",
        "OntologyCandidate",
        "OntologyClass",
        "OntologyProperty",
        "OntologyRelationship",
        "SemanticArtifact",
        "SemanticArtifactDraftInput",
        "SemanticArtifactSummary",
        "SemanticPromptContext",
        "ShaclCandidate",
        "ShaclPropertyConstraint",
        "ShaclShape",
        "VocabularyCandidate",
        "VocabularyTerm",
    ],
    ".qualification": [
        "CanonicalEntityRecord",
        "CanonicalRelationRecord",
        "CurationDecisionResult",
        "CurationPreview",
        "GraphProjectionResult",
        "GraphProjectionSnapshot",
        "QualificationCase",
        "QualificationRunResult",
    ],
    ".models": [
        "AgentRunResponse",
        "ArchiveResult",
        "AskResponse",
        "ChatResponse",
        "DebateRunResponse",
        "EvidenceBundle",
        "EntityOverride",
        "ExecutionPlan",
        "ExecutionResult",
        "FulltextIndexResponse",
        "GraphRef",
        "GraphTarget",
        "Memory",
        "MemoryCreateResult",
        "PlatformChatResponse",
        "PlatformSessionResponse",
        "RawIngestError",
        "RawIngestResult",
        "RawIngestWarning",
        "ReasoningPolicy",
        "RunMetadata",
        "SearchResponse",
        "SearchResult",
        "SemanticRunRecord",
        "SemanticRunResponse",
        "StrategyDecision",
        "SupportAssessment",
    ],
}

_NAME_TO_MODULE = {
    name: module_name
    for module_name, exported_names in _MODULE_EXPORTS.items()
    for name in exported_names
}

_CALLABLE_SUBMODULE_EXPORTS: Mapping[str, str] = {
    "agents": ".agents",
    "debate": ".debate",
    "semantic": ".semantic",
}


class _CallableSubmoduleProxy:
    """Expose a root convenience call and submodule attributes together."""

    def __init__(self, package_name: str, export_name: str, submodule_name: str) -> None:
        self._package_name = package_name
        self._export_name = export_name
        self._submodule_name = submodule_name

    def __call__(self, *args, **kwargs):
        api_module = import_module(".api", self._package_name)
        api_fn = getattr(api_module, self._export_name)
        return api_fn(*args, **kwargs)

    def __getattr__(self, name: str):
        submodule = import_module(self._submodule_name, self._package_name)
        return getattr(submodule, name)

    def __dir__(self):
        submodule = import_module(self._submodule_name, self._package_name)
        return sorted(set(dir(submodule)))

    def __repr__(self) -> str:
        return (
            f"<callable submodule proxy {self._package_name}.{self._export_name}"
            f" -> {self._submodule_name}>"
        )


class _SeochoModule(ModuleType):
    """Preserve convenience-call exports even after submodule imports."""

    def __getattribute__(self, name: str):
        if name in _CALLABLE_SUBMODULE_EXPORTS:
            module_dict = ModuleType.__getattribute__(self, "__dict__")
            proxy_cache = module_dict.setdefault("_callable_submodule_proxy_cache", {})
            proxy = proxy_cache.get(name)
            if proxy is None:
                proxy = _CallableSubmoduleProxy(
                    package_name=ModuleType.__getattribute__(self, "__name__"),
                    export_name=name,
                    submodule_name=_CALLABLE_SUBMODULE_EXPORTS[name],
                )
                proxy_cache[name] = proxy
            return proxy
        return ModuleType.__getattribute__(self, name)


def __getattr__(name: str):
    if name == "__version__":
        return __version__
    module_name = _NAME_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__) | set(_NAME_TO_MODULE))


# ---------------------------------------------------------------------------
# __all__ — Tier 1 only (the 95% case).
#
# Everything outside this list is still importable via explicit
# `from seocho import <name>`; we just exclude it from `from seocho import *`
# and from default surface advertising. This matches the "Public Plugin
# Surface" docstring above — narrow + stable.
#
# Tier 2/3 (advanced, governance, evaluation, semantic artifacts, ontology
# context internals) remain available via their submodules:
#   from seocho.api import advanced, debate, react, semantic, ...
#   from seocho.agent_config import AgentConfig, AGENT_PRESETS
#   from seocho.semantic.artifacts import SemanticArtifact, ...
#   from seocho.tracing import SessionTrace
# Adding back to Tier 1 must be a deliberate decision (PR + reviewer).
# ---------------------------------------------------------------------------

__all__ = [
    # Core client + version
    "__version__",
    "Seocho",
    "AsyncSeocho",
    # Lifecycle objects
    "Memory",
    "Session",
    # Ontology primitives
    "Ontology",
    "NodeDef",
    "RelDef",
    "Property",
    "P",
    "PropertyType",
    "Cardinality",
    # Ontology control plane
    "CompiledOntologyProfile",
    "OntologyControlPlane",
    "OntologyProfile",
    "OntologyProfileEvaluation",
    "OntologyProfileRegistry",
    "OntologyProfileSelection",
    "OntologySignal",
    # Store ABCs + concrete vector backends
    "VectorStore",
    "FAISSVectorStore",
    "LanceDBVectorStore",
    "create_vector_store",
    # LLM / embedding plugin surface
    "LLMBackend",
    "EmbeddingBackend",
    "OpenAIBackend",
    "create_llm_backend",
    "create_embedding_backend",
    # Observability
    "enable_tracing",
    "disable_tracing",
    "flush_tracing",
    "start_span",
    "capture_text",
    # Errors users catch
    "SeochoError",
    "SeochoConnectionError",
    "SeochoHTTPError",
    "OntologyDriftError",
]

import sys as _sys

_sys.modules[__name__].__class__ = _SeochoModule
