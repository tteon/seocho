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

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


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


# ======================================================================
# Multi-agent extraction strategies
# ======================================================================


class ParallelExtractionStrategy(IndexingStrategy):
    """Run extraction with multiple LLM backends in parallel, merge results.

    All models extract from the same text. Results are union-merged:
    nodes from all models are combined, duplicates removed by label+name.

    Usage::

        from seocho import AgentConfig
        from seocho.agent_config import ParallelExtractionStrategy
        from seocho.store import OpenAIBackend

        strategy = ParallelExtractionStrategy(
            models=[
                OpenAIBackend(model="gpt-4o"),
                OpenAIBackend(model="gpt-4o-mini"),
            ],
        )
        config = AgentConfig(custom_indexing_strategy=strategy)
    """

    def __init__(self, models: Optional[List[Any]] = None) -> None:
        self.models = models or []
        self._extra_results: List[Dict[str, Any]] = []

    def extract_parallel(self, text: str, ontology: Any, primary_result: Dict[str, Any]) -> Dict[str, Any]:
        """Run extraction on additional models and merge with primary result."""
        from .query.strategy import ExtractionStrategy

        all_nodes = list(primary_result.get("nodes", []))
        all_rels = list(primary_result.get("relationships", []))

        for model_backend in self.models:
            try:
                strategy = ExtractionStrategy(ontology)
                system, user = strategy.render(text)
                response = model_backend.complete(
                    system=system, user=user,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )
                extra = response.json()
                all_nodes.extend(extra.get("nodes", []))
                all_rels.extend(extra.get("relationships", []))
            except Exception as exc:
                logger.warning("Parallel model failed: %s", exc)

        # Deduplicate nodes by label+name
        merged_nodes = _dedup_nodes(all_nodes)
        return {"nodes": merged_nodes, "relationships": all_rels}

    def post_extract(self, nodes, relationships, score, ontology):
        return nodes, relationships, True


class EnsembleExtractionStrategy(IndexingStrategy):
    """Run extraction with multiple models, keep only nodes agreed by majority.

    Each model extracts independently. A node is kept only if at least
    ``threshold`` fraction of models produced it (by label+name match).

    Usage::

        strategy = EnsembleExtractionStrategy(
            models=[model_a, model_b, model_c],
            threshold=0.5,  # keep if >= 50% of models agree
        )
    """

    def __init__(
        self,
        models: Optional[List[Any]] = None,
        threshold: float = 0.5,
    ) -> None:
        self.models = models or []
        self.threshold = threshold

    def extract_ensemble(self, text: str, ontology: Any) -> Dict[str, Any]:
        """Run all models and vote on nodes."""
        from .query.strategy import ExtractionStrategy

        all_extractions: List[Dict[str, Any]] = []
        for model_backend in self.models:
            try:
                strategy = ExtractionStrategy(ontology)
                system, user = strategy.render(text)
                response = model_backend.complete(
                    system=system, user=user,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )
                all_extractions.append(response.json())
            except Exception as exc:
                logger.warning("Ensemble model failed: %s", exc)

        if not all_extractions:
            return {"nodes": [], "relationships": []}

        # Vote: count how many models produced each node (by label+name)
        node_votes: Dict[str, int] = {}
        node_map: Dict[str, Dict] = {}
        for ext in all_extractions:
            for node in ext.get("nodes", []):
                key = _node_key(node)
                node_votes[key] = node_votes.get(key, 0) + 1
                node_map[key] = node  # keep latest version

        min_votes = max(1, int(len(all_extractions) * self.threshold))
        kept_nodes = [
            node_map[key] for key, votes in node_votes.items()
            if votes >= min_votes
        ]

        # Relationships: keep all unique ones
        all_rels = []
        seen_rels = set()
        for ext in all_extractions:
            for rel in ext.get("relationships", []):
                rkey = f"{rel.get('source')}-{rel.get('type')}-{rel.get('target')}"
                if rkey not in seen_rels:
                    seen_rels.add(rkey)
                    all_rels.append(rel)

        return {"nodes": kept_nodes, "relationships": all_rels}

    def post_extract(self, nodes, relationships, score, ontology):
        return nodes, relationships, True


def _node_key(node: Dict) -> str:
    """Create a dedup key for a node."""
    label = node.get("label", "")
    name = node.get("properties", {}).get("name", node.get("id", ""))
    return f"{label}::{name}".lower()


def _dedup_nodes(nodes: List[Dict]) -> List[Dict]:
    """Remove duplicate nodes by label+name, keeping the last one."""
    seen: Dict[str, Dict] = {}
    for node in nodes:
        key = _node_key(node)
        seen[key] = node
    return list(seen.values())
