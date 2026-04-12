"""
seocho.query — Control Plane: ontology-aware querying and answer synthesis.

Where to look:
- ``strategy``: ExtractionStrategy, QueryStrategy, LinkingStrategy
  (ontology → LLM prompt generation for each phase)
- ``PromptTemplate``: user-customizable prompt structure
- ``PRESET_PROMPTS``: domain-specific templates (finance, legal, medical, research)

If you want to improve Cypher generation or answer quality, start here.
"""

from .strategy import (
    ExtractionStrategy,
    LinkingStrategy,
    PRESET_PROMPTS,
    PromptStrategy,
    PromptTemplate,
    QueryStrategy,
    RDFQueryStrategy,
)

__all__ = [
    "PromptStrategy",
    "PromptTemplate",
    "PRESET_PROMPTS",
    "ExtractionStrategy",
    "QueryStrategy",
    "LinkingStrategy",
]
