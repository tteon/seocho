"""Backward-compatible re-export — canonical location is ``seocho.store.graph``."""
from seocho.store.graph import GraphStore, Neo4jGraphStore  # noqa: F401

__all__ = ["GraphStore", "Neo4jGraphStore"]
