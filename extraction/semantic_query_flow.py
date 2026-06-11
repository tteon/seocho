"""Compatibility shim for the semantic query flow.

The canonical 4-agent semantic query orchestration (router → LPG → RDF →
answer generation) and its supporting validators, strategy chooser, entity
resolver, and contracts now live in :mod:`seocho.query.*` — see the
DECISION_LOG entry "move SemanticAgentFlow and 14 supporting classes from
extraction/semantic_query_flow.py to seocho/query/*".

This module previously carried a full local copy of those classes, but every
public name was reassigned to its ``seocho.query`` counterpart at import time,
so the ~2.6k lines of local definitions were dead, shadowed duplicates. They
have been removed; this module now simply re-exports the canonical
implementations so existing flat imports keep working::

    from semantic_query_flow import SemanticAgentFlow, QueryRouterAgent, SemanticEntityResolver
"""

from __future__ import annotations

from seocho.query.answering import build_evidence_bundle, infer_question_intent
from seocho.query.constraints import SemanticConstraintSliceBuilder
from seocho.query.contracts import (
    CypherPlan,
    InsufficiencyAssessment,
    IntentSpec,
)
from seocho.query.cypher_validator import CypherQueryValidator
from seocho.query.insufficiency import QueryInsufficiencyClassifier
from seocho.query.run_registry import RunMetadataRegistry
from seocho.query.semantic_agents import (
    AnswerGenerationAgent,
    LPGAgent,
    QueryRouterAgent,
    RDFAgent,
    SemanticEntityResolver,
)
from seocho.query.semantic_flow import SemanticAgentFlow
from seocho.query.strategy_chooser import (
    ExecutionStrategyChooser,
    IntentSupportValidator,
)

__all__ = [
    "AnswerGenerationAgent",
    "CypherPlan",
    "CypherQueryValidator",
    "ExecutionStrategyChooser",
    "InsufficiencyAssessment",
    "IntentSpec",
    "IntentSupportValidator",
    "LPGAgent",
    "QueryInsufficiencyClassifier",
    "QueryRouterAgent",
    "RDFAgent",
    "RunMetadataRegistry",
    "SemanticAgentFlow",
    "SemanticConstraintSliceBuilder",
    "SemanticEntityResolver",
    "build_evidence_bundle",
    "infer_question_intent",
]
