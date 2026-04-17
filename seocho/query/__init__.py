"""
seocho.query — Control Plane: ontology-aware querying and answer synthesis.

Where to look:
- ``strategy``: ExtractionStrategy, QueryStrategy, LinkingStrategy
  (ontology → LLM prompt generation for each phase)
- ``PromptTemplate``: user-customizable prompt structure
- ``PRESET_PROMPTS``: domain-specific templates (finance, legal, medical, research)

If you want to improve Cypher generation or answer quality, start here.
"""

from .agent_factory import AgentConfig, AgentFactory
from .answering import QueryAnswerSynthesizer, build_evidence_bundle, infer_question_intent
from .constraints import SemanticConstraintSliceBuilder
from .contracts import (
    CypherPlan,
    InsufficiencyAssessment,
    IntentSpec,
    QueryAttempt,
    QueryExecution,
    QueryPlan,
)
from .cypher_validator import CypherQueryValidator
from .cypher_builder import CypherBuilder
from .executor import GraphQueryExecutor
from .insufficiency import QueryInsufficiencyClassifier
from .intent import INTENT_CATALOG
from .planner import DeterministicQueryPlanner
from .query_proxy import NullQueryPolicy, QueryPolicy, QueryProxy, QueryRequest
from .run_registry import RunMetadataRegistry
from .semantic_flow import SemanticAgentFlow
from .semantic_agents import (
    AnswerGenerationAgent,
    LPGAgent,
    QueryRouterAgent,
    RDFAgent,
    SemanticEntityResolver,
)
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
from .strategy_chooser import ExecutionStrategyChooser, IntentSupportValidator

__all__ = [
    "IntentSpec",
    "AgentConfig",
    "AgentFactory",
    "INTENT_CATALOG",
    "QueryPlan",
    "QueryExecution",
    "QueryAttempt",
    "CypherPlan",
    "InsufficiencyAssessment",
    "DeterministicQueryPlanner",
    "GraphQueryExecutor",
    "QueryAnswerSynthesizer",
    "build_evidence_bundle",
    "infer_question_intent",
    "QueryProxy",
    "QueryRequest",
    "QueryPolicy",
    "NullQueryPolicy",
    "SemanticConstraintSliceBuilder",
    "RunMetadataRegistry",
    "SemanticAgentFlow",
    "SemanticEntityResolver",
    "QueryRouterAgent",
    "LPGAgent",
    "RDFAgent",
    "AnswerGenerationAgent",
    "CypherQueryValidator",
    "QueryInsufficiencyClassifier",
    "IntentSupportValidator",
    "ExecutionStrategyChooser",
    "PromptStrategy",
    "PromptTemplate",
    "PRESET_PROMPTS",
    "ExtractionStrategy",
    "QueryStrategy",
    "LinkingStrategy",
]
