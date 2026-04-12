"""
Agent configuration — control how indexing and querying agents behave.

Two levels of customization:

1. **Config dict** (beginners): adjust knobs without writing code::

    s = Seocho(
        ontology=onto, graph_store=store, llm=llm,
        agent_config=AgentConfig(
            # Indexing agent
            extraction_strategy="domain",   # "general", "domain", "multi_pass"
            extraction_quality_threshold=0.7,
            extraction_retry_on_low_quality=True,
            linking_strategy="llm",         # "llm", "embedding", "none"
            validation_on_fail="retry",     # "reject", "retry", "relax", "warn"

            # Query agent
            query_strategy="llm_cypher",    # "llm_cypher", "template", "hybrid"
            answer_style="evidence",        # "concise", "evidence", "table"
            reasoning_mode=True,
            repair_budget=3,
            routing="auto",                 # "auto", "lpg_only", "rdf_only"
        ),
    )

2. **Strategy injection** (advanced): replace components::

    class MyExtractor(IndexingStrategy):
        def extract(self, text, ontology): ...

    s = Seocho(
        ontology=onto, graph_store=store, llm=llm,
        agent_config=AgentConfig(
            custom_indexing_strategy=MyExtractor(),
            custom_query_strategy=MyQueryStrategy(),
        ),
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ======================================================================
# Strategy ABCs — for advanced users who want to replace components
# ======================================================================

class IndexingStrategy:
    """Base class for custom indexing agent strategies.

    Override any method to customize that step of the indexing pipeline.
    """

    def should_extract(self, text: str, metadata: Dict[str, Any]) -> bool:
        """Decide whether to extract from this text. Return False to skip."""
        return True

    def post_extract(
        self,
        nodes: List[Dict],
        relationships: List[Dict],
        score: float,
        ontology: Any,
    ) -> tuple:
        """Called after extraction. Can filter, enrich, or retry.

        Returns (nodes, relationships, should_continue).
        """
        return nodes, relationships, True

    def on_validation_fail(
        self,
        nodes: List[Dict],
        relationships: List[Dict],
        errors: List[str],
    ) -> str:
        """Decide what to do when SHACL validation fails.

        Returns: "reject", "retry", "relax", or "warn".
        """
        return "warn"

    def on_linking(
        self,
        nodes: List[Dict],
        relationships: List[Dict],
    ) -> tuple:
        """Called during entity linking. Can override linking behavior.

        Returns (nodes, relationships).
        """
        return nodes, relationships


class QueryAgentStrategy:
    """Base class for custom query agent strategies.

    Override any method to customize query behavior.
    """

    def choose_route(self, question: str, ontology: Any) -> str:
        """Decide query route. Returns "lpg", "rdf", or "hybrid"."""
        return "lpg"

    def post_query(
        self,
        question: str,
        cypher: str,
        results: List[Dict],
    ) -> tuple:
        """Called after Cypher execution. Can filter or enrich results.

        Returns (results, should_repair).
        """
        should_repair = len(results) == 0
        return results, should_repair

    def format_answer(
        self,
        question: str,
        results: List[Dict],
        style: str,
    ) -> Optional[str]:
        """Custom answer formatting. Return None to use default."""
        return None


# ======================================================================
# AgentConfig — unified configuration
# ======================================================================

@dataclass
class AgentConfig:
    """Configuration for indexing and query agent behavior.

    Parameters
    ----------

    Indexing agent:

    extraction_strategy:
        How to extract entities.
        - ``"general"`` — default prompt
        - ``"domain"`` — use ``extraction_prompt`` preset
        - ``"multi_pass"`` — extract ontology candidates first, then entities
    extraction_quality_threshold:
        Minimum score (0.0–1.0) to accept extraction. Below this,
        behavior depends on ``extraction_retry_on_low_quality``.
    extraction_retry_on_low_quality:
        If True, retry extraction with higher temperature when score
        is below threshold.
    linking_strategy:
        How to deduplicate entities.
        - ``"llm"`` — ask LLM to identify duplicates
        - ``"embedding"`` — cosine similarity matching
        - ``"none"`` — skip linking
    validation_on_fail:
        What to do when SHACL validation fails.
        - ``"reject"`` — skip this chunk
        - ``"retry"`` — re-extract with guidance about the error
        - ``"relax"`` — strip failing properties, keep valid parts
        - ``"warn"`` — write anyway with warning (default)

    Query agent:

    query_strategy:
        How to generate Cypher queries.
        - ``"llm_cypher"`` — LLM generates full Cypher (default)
        - ``"template"`` — deterministic Cypher templates
        - ``"hybrid"`` — template first, LLM fallback
    answer_style:
        How to format answers.
        - ``"concise"`` — short factual answer
        - ``"evidence"`` — answer with supporting facts
        - ``"table"`` — structured table format
    reasoning_mode:
        Enable automatic query repair on empty results.
    repair_budget:
        Max repair attempts.
    routing:
        Query routing strategy.
        - ``"auto"`` — detect from question keywords
        - ``"lpg_only"`` — always use Cypher
        - ``"rdf_only"`` — always use SPARQL

    Advanced (strategy injection):

    custom_indexing_strategy:
        Replace the default indexing agent with your own.
    custom_query_strategy:
        Replace the default query agent with your own.
    """

    # --- Indexing agent ---
    extraction_strategy: str = "general"
    extraction_quality_threshold: float = 0.0
    extraction_retry_on_low_quality: bool = False
    extraction_max_retries: int = 2
    linking_strategy: str = "llm"
    validation_on_fail: str = "warn"

    # --- Query agent ---
    query_strategy: str = "llm_cypher"
    answer_style: str = "concise"
    reasoning_mode: bool = False
    repair_budget: int = 2
    routing: str = "auto"

    # --- Advanced: strategy injection ---
    custom_indexing_strategy: Optional[IndexingStrategy] = None
    custom_query_strategy: Optional[QueryAgentStrategy] = None

    # --- Extra params (forwarded as-is) ---
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "extraction_strategy": self.extraction_strategy,
            "extraction_quality_threshold": self.extraction_quality_threshold,
            "extraction_retry_on_low_quality": self.extraction_retry_on_low_quality,
            "linking_strategy": self.linking_strategy,
            "validation_on_fail": self.validation_on_fail,
            "query_strategy": self.query_strategy,
            "answer_style": self.answer_style,
            "reasoning_mode": self.reasoning_mode,
            "repair_budget": self.repair_budget,
            "routing": self.routing,
            "has_custom_indexing": self.custom_indexing_strategy is not None,
            "has_custom_query": self.custom_query_strategy is not None,
        }


# ======================================================================
# Presets
# ======================================================================

AGENT_PRESETS: Dict[str, AgentConfig] = {
    "default": AgentConfig(),

    "strict": AgentConfig(
        extraction_quality_threshold=0.8,
        extraction_retry_on_low_quality=True,
        validation_on_fail="reject",
        reasoning_mode=True,
        repair_budget=3,
        answer_style="evidence",
    ),

    "fast": AgentConfig(
        extraction_strategy="general",
        linking_strategy="none",
        validation_on_fail="warn",
        query_strategy="template",
        answer_style="concise",
        reasoning_mode=False,
    ),

    "research": AgentConfig(
        extraction_strategy="domain",
        extraction_quality_threshold=0.7,
        extraction_retry_on_low_quality=True,
        linking_strategy="llm",
        validation_on_fail="retry",
        reasoning_mode=True,
        repair_budget=3,
        answer_style="evidence",
    ),
}
