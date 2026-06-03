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
    AnswerShape,
    InsufficiencyAssessment,
    IntentSpec,
    QueryAttempt,
    QueryExecution,
    QueryPlan,
    RouteProfile,
)
from .graph_cot_contracts import (
    AnswerDraft,
    GraphCoTFinalAnswer,
    GraphCoTQuestionFrame,
    GuardrailFinding,
    GuardrailVerdict,
    QueryEvidencePacket,
    SupervisorDirective,
)
from .graph_cot_design import (
    GraphCoTAgentSpec,
    GraphCoTToolSpec,
    build_graph_cot_agent_specs,
    graph_cot_answer_generation_system_prompt,
    graph_cot_answer_guardrail_system_prompt,
    graph_cot_supervisor_system_prompt,
    graph_cot_text2cypher_system_prompt,
)
from .graph_cot_flow import (
    AnswerGuardrailAgent,
    GraphCoTAnswerGenerationAgent,
    GraphCoTQueryOrchestrator,
    GraphCoTRetrievalResult,
    QuerySupervisorAgent,
    Text2CypherAgent,
)
from .cypher_validator import CypherQueryValidator
from .cypher_builder import CypherBuilder
from .evidence_swarm import (
    EvidenceSwarmReport,
    EvidenceSwarmScout,
    build_evidence_swarm_report,
)
from .evidence_grounding import (
    GROUNDING_OPTIMIZER_PROFILES,
    build_grounded_synthesis_prompt,
    grounding_optimizer_receipt,
)
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
    "RouteProfile",
    "AnswerShape",
    "AgentConfig",
    "AgentFactory",
    "INTENT_CATALOG",
    "QueryPlan",
    "QueryExecution",
    "QueryAttempt",
    "CypherPlan",
    "InsufficiencyAssessment",
    "GraphCoTQuestionFrame",
    "SupervisorDirective",
    "QueryEvidencePacket",
    "AnswerDraft",
    "GuardrailFinding",
    "GuardrailVerdict",
    "GraphCoTFinalAnswer",
    "GraphCoTToolSpec",
    "GraphCoTAgentSpec",
    "build_graph_cot_agent_specs",
    "graph_cot_supervisor_system_prompt",
    "graph_cot_text2cypher_system_prompt",
    "graph_cot_answer_generation_system_prompt",
    "graph_cot_answer_guardrail_system_prompt",
    "QuerySupervisorAgent",
    "Text2CypherAgent",
    "GraphCoTAnswerGenerationAgent",
    "AnswerGuardrailAgent",
    "GraphCoTRetrievalResult",
    "GraphCoTQueryOrchestrator",
    "DeterministicQueryPlanner",
    "GraphQueryExecutor",
    "EvidenceSwarmReport",
    "EvidenceSwarmScout",
    "build_evidence_swarm_report",
    "GROUNDING_OPTIMIZER_PROFILES",
    "build_grounded_synthesis_prompt",
    "grounding_optimizer_receipt",
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
