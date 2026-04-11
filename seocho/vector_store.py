"""Backward-compatible re-export — canonical location is ``seocho.store.vector``."""
from seocho.store.vector import FAISSVectorStore, VectorSearchResult, VectorStore  # noqa: F401

__all__ = ["VectorStore", "FAISSVectorStore", "VectorSearchResult"]
