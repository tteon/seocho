"""
seocho.store — Storage backend abstractions.

Where to look:
- ``graph``: embedded LadybugDB and Neo4j/DozerDB graph stores
  (write nodes/rels, query Cypher)
- ``vector``: FAISS / LanceDB vector similarity search
- ``llm``: OpenAI-compatible LLM and embedding backends
"""

from .graph import GraphStore, LadybugGraphStore, Neo4jGraphStore
from .llm import (
    DeepSeekBackend,
    EmbeddingBackend,
    GrokBackend,
    KimiBackend,
    LLMBackend,
    LLMResponse,
    OpenAIBackend,
    OpenAICompatibleBackend,
    OpenAICompatibleEmbeddingBackend,
    ProviderSpec,
    create_embedding_backend,
    create_llm_backend,
    get_provider_spec,
    list_provider_specs,
)
from .vector import (
    FAISSVectorStore,
    LanceDBVectorStore,
    VectorSearchResult,
    VectorStore,
    create_vector_store,
)

__all__ = [
    "GraphStore",
    "LadybugGraphStore",
    "Neo4jGraphStore",
    "ProviderSpec",
    "LLMBackend",
    "EmbeddingBackend",
    "LLMResponse",
    "OpenAICompatibleBackend",
    "OpenAICompatibleEmbeddingBackend",
    "OpenAIBackend",
    "DeepSeekBackend",
    "KimiBackend",
    "GrokBackend",
    "get_provider_spec",
    "list_provider_specs",
    "create_llm_backend",
    "create_embedding_backend",
    "VectorStore",
    "FAISSVectorStore",
    "LanceDBVectorStore",
    "create_vector_store",
    "VectorSearchResult",
]
