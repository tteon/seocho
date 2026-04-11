"""
seocho.query — Control Plane: ontology-aware querying and answer synthesis.

Where to look:
- ``strategy``: ExtractionStrategy, QueryStrategy, LinkingStrategy
  (ontology → LLM prompt generation for each phase)

If you want to improve Cypher generation or answer quality, start here.
"""

from .strategy import (
    ExtractionStrategy,
    LinkingStrategy,
    PromptStrategy,
    QueryStrategy,
)

__all__ = [
    "PromptStrategy",
    "ExtractionStrategy",
    "QueryStrategy",
    "LinkingStrategy",
]
