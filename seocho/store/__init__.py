"""
seocho.store — Storage backend abstractions.

Where to look:
- ``graph``: Neo4j/DozerDB graph store (write nodes/rels, query Cypher)
- ``vector``: FAISS vector similarity search
- ``llm``: LLM completion backends (OpenAI, etc.)
"""

from .graph import GraphStore, Neo4jGraphStore
from .llm import LLMBackend, LLMResponse, OpenAIBackend
from .vector import FAISSVectorStore, VectorSearchResult, VectorStore

__all__ = [
    "GraphStore",
    "Neo4jGraphStore",
    "LLMBackend",
    "LLMResponse",
    "OpenAIBackend",
    "VectorStore",
    "FAISSVectorStore",
    "VectorSearchResult",
]
