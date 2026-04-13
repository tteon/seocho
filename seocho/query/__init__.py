"""
seocho.query — Control Plane: ontology-aware querying and answer synthesis.

Where to look:
- ``strategy``: ExtractionStrategy, QueryStrategy, LinkingStrategy
  (ontology → LLM prompt generation for each phase)
- ``PromptTemplate``: user-customizable prompt structure
- ``PRESET_PROMPTS``: domain-specific templates (finance, legal, medical, research)

If you want to improve Cypher generation or answer quality, start here.
"""

from .answering import QueryAnswerSynthesizer, build_evidence_bundle, infer_question_intent
from .contracts import QueryAttempt, QueryExecution, QueryPlan
from .cypher_builder import CypherBuilder
from .executor import GraphQueryExecutor
from .planner import DeterministicQueryPlanner
from .strategy import (
    CATEGORY_PROMPT_MAP,
    ExtractionStrategy,
    LinkingStrategy,
    PRESET_PROMPTS,
    PromptStrategy,
    PromptTemplate,
    QueryStrategy,
    RDFQueryStrategy,
)

__all__ = [
    "QueryPlan",
    "QueryExecution",
    "QueryAttempt",
    "DeterministicQueryPlanner",
    "GraphQueryExecutor",
    "QueryAnswerSynthesizer",
    "build_evidence_bundle",
    "infer_question_intent",
    "PromptStrategy",
    "PromptTemplate",
    "PRESET_PROMPTS",
    "ExtractionStrategy",
    "QueryStrategy",
    "LinkingStrategy",
]
