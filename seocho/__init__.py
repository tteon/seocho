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
from typing import Dict, Iterable

try:
    __version__ = version("seocho")
except PackageNotFoundError:
    __version__ = "0.1.0"

_MODULE_EXPORTS: Dict[str, Iterable[str]] = {
    ".api": [
        "advanced",
        "add",
        "add_with_details",
        "agents",
        "apply_artifact",
        "ask",
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
        "plan",
        "platform_chat",
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
        "load_indexing_design_spec",
    ],
    ".client": [
        "AsyncSeocho",
        "ExecutionPlanBuilder",
        "Seocho",
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
        "apply_ontology_context_to_graph_payload",
        "assess_graph_ontology_context_status",
        "assess_ontology_context_mismatch",
        "compile_ontology_context",
        "ontology_context_graph_properties",
        "query_ontology_context_mismatch",
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
        "configure_tracing_from_env",
        "current_backend_names",
        "disable_tracing",
        "enable_tracing",
        "flush_tracing",
        "is_backend_enabled",
        "is_tracing_enabled",
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
    ".models": [
        "AgentRunResponse",
        "ArchiveResult",
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


__all__ = [
    "__version__",
    "AGENT_PRESETS",
    "AgentConfig",
    "AgentDesignSpec",
    "AgentRunResponse",
    "ApprovedArtifacts",
    "ArchiveResult",
    "ArtifactDiff",
    "ArtifactValidationMessage",
    "ArtifactValidationResult",
    "AsyncSeocho",
    "BatchIndexingResult",
    "Cardinality",
    "ChatResponse",
    "CompiledOntologyContext",
    "DebateRunResponse",
    "DeepSeekBackend",
    "EmbeddingBackend",
    "EnsembleExtractionStrategy",
    "EntityOverride",
    "EvaluationBaselineResult",
    "EvaluationCaseResult",
    "EvaluationMatrixSummary",
    "EvidenceBundle",
    "ExecutionPlan",
    "ExecutionPlanBuilder",
    "ExecutionResult",
    "FAISSVectorStore",
    "FulltextIndexResponse",
    "GraphRef",
    "GraphTarget",
    "GrokBackend",
    "IndexingResult",
    "IndexingStrategy",
    "KimiBackend",
    "KnownEntity",
    "LLMBackend",
    "LLMResponse",
    "LanceDBVectorStore",
    "LocalRuntimeStatus",
    "ManualGoldCase",
    "Memory",
    "MemoryCreateResult",
    "NodeDef",
    "NodeDefinition",
    "Ontology",
    "OntologyBinding",
    "OntologyContextCache",
    "OntologyContextDescriptor",
    "OntologyEvidenceState",
    "OntologyPolicyDecision",
    "OntologyRunContext",
    "OntologyCandidate",
    "OntologyClass",
    "OntologyProperty",
    "OntologyRelationship",
    "OpenAIBackend",
    "OpenAICompatibleBackend",
    "OpenAICompatibleEmbeddingBackend",
    "P",
    "ParallelExtractionStrategy",
    "PlatformChatResponse",
    "PlatformSessionResponse",
    "PortablePromptTemplate",
    "Property",
    "PropertyType",
    "ProviderSpec",
    "QwenBackend",
    "QueryAgentStrategy",
    "RawIngestError",
    "RawIngestResult",
    "RawIngestWarning",
    "ReasoningPolicy",
    "RelDef",
    "RelationshipDefinition",
    "RoutingPolicy",
    "RunMetadata",
    "SearchResponse",
    "SearchResult",
    "SemanticArtifact",
    "SemanticArtifactDraftInput",
    "SemanticArtifactSummary",
    "SemanticEvaluationHarness",
    "SemanticPromptContext",
    "SemanticRunRecord",
    "SemanticRunResponse",
    "Seocho",
    "SeochoConnectionError",
    "SeochoError",
    "SeochoHTTPError",
    "Session",
    "SessionTrace",
    "ShaclCandidate",
    "ShaclPropertyConstraint",
    "ShaclShape",
    "StrategyDecision",
    "SupportAssessment",
    "VectorSearchResult",
    "VectorStore",
    "VocabularyCandidate",
    "VocabularyTerm",
    "add",
    "add_with_details",
    "advanced",
    "agents",
    "apply_artifact",
    "apply_ontology_context_to_graph_payload",
    "assess_graph_ontology_context_status",
    "assess_ontology_context_mismatch",
    "ask",
    "begin_session",
    "build_runtime_bundle",
    "build_local_ontology_run_context",
    "build_runtime_ontology_run_context",
    "chat",
    "close",
    "configure",
    "connect",
    "compile_ontology_context",
    "create_bundle_runtime_app",
    "create_client_from_runtime_bundle",
    "create_embedding_backend",
    "create_llm_backend",
    "create_vector_store",
    "current_backend_names",
    "databases",
    "debate",
    "delete",
    "disable_tracing",
    "enable_tracing",
    "ensure_fulltext_indexes",
    "execute",
    "flush_tracing",
    "get",
    "get_client",
    "get_provider_spec",
    "graphs",
    "health",
    "is_backend_enabled",
    "is_tracing_enabled",
    "IndexingDesignSpec",
    "IndexingOntologyBinding",
    "list_provider_specs",
    "load_agent_design_spec",
    "load_indexing_design_spec",
    "ontology_context_graph_properties",
    "plan",
    "platform_chat",
    "query_ontology_context_mismatch",
    "raw_ingest",
    "react",
    "reset_session",
    "router",
    "search",
    "semantic",
    "semantic_run",
    "semantic_runs",
    "session_history",
]
