"""
Prompt strategies — ontology-driven prompt generation for extraction,
querying, and entity linking.

Each strategy takes an :class:`~seocho.ontology.Ontology` and produces
ready-to-use system/user prompts for the respective LLM call.

The strategies are designed to be composable::

    from seocho import Ontology
    from seocho.prompt_strategy import ExtractionStrategy, QueryStrategy

    onto = Ontology.from_yaml("schema.yaml")

    ext = ExtractionStrategy(onto)
    system, user = ext.render("텍스트를 분석해주세요")

    qs = QueryStrategy(onto, schema_info={"node_count": 1200})
    system, user = qs.render("이재용은 어디에서 일하나요?")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from seocho.ontology import Ontology


def _sanitize_prompt_value(value: Any) -> str:
    """Sanitize a user-provided value before inserting into a prompt.

    Strips control characters and truncates to prevent prompt injection.
    """
    text = str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if len(text) > 2000:
        text = text[:2000] + "... (truncated)"
    return text


# ---------------------------------------------------------------------------
# PromptTemplate — user-customizable prompt structure
# ---------------------------------------------------------------------------


@dataclass
class PromptTemplate:
    """User-defined prompt template with Jinja2-style ``{{variable}}`` placeholders.

    Available variables (auto-injected from ontology):

    - ``{{ontology_name}}`` — ontology name
    - ``{{entity_types}}`` — formatted list of node types + properties
    - ``{{relationship_types}}`` — formatted relationship listing
    - ``{{constraints_summary}}`` — property constraints
    - ``{{graph_schema}}`` — full schema block (query mode)
    - ``{{query_hints}}`` — constraint-derived hints (query mode)
    - ``{{text}}`` — input text (user prompt)

    Example::

        custom = PromptTemplate(
            system="You are a FIBO expert. Extract financial entities.\\n{{entity_types}}",
            user="Document:\\n{{text}}",
        )
        s = Seocho(ontology=onto, graph_store=store, llm=llm,
                   extraction_prompt=custom)
    """

    system: str
    user: str = "{{text}}"

    def render(self, context: Dict[str, str], text: str) -> tuple[str, str]:
        """Render template with context variables."""
        system = self.system
        user = self.user
        for key, value in context.items():
            system = system.replace("{{" + key + "}}", str(value))
            user = user.replace("{{" + key + "}}", str(value))
        system = system.replace("{{text}}", text)
        user = user.replace("{{text}}", text)
        return system, user


# ---------------------------------------------------------------------------
# Preset prompts for common domains
# ---------------------------------------------------------------------------

PRESET_PROMPTS: Dict[str, PromptTemplate] = {
    "general": PromptTemplate(
        system=(
            "You are an expert entity extraction system.\n"
            'You are working with the "{{ontology_name}}" ontology.\n\n'
            "Extract entities of the following types:\n{{entity_types}}\n\n"
            "Extract relationships of the following types:\n{{relationship_types}}\n\n"
            "{{constraints_summary}}\n\n"
            'Return JSON with "nodes" and "relationships" keys.\n'
            'Nodes: {"id": "unique_id", "label": "EntityType", "properties": {"name": "..."}}\n'
            'Relationships: {"source": "id", "target": "id", "type": "TYPE", "properties": {}}'
        ),
        user="Text to extract:\n{{text}}",
    ),
    "finance": PromptTemplate(
        system=(
            "You are a financial domain expert specializing in the Financial Industry Business Ontology (FIBO).\n"
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Extract financial entities of these types:\n{{entity_types}}\n\n"
            "Extract financial relationships:\n{{relationship_types}}\n\n"
            "Pay special attention to:\n"
            "- Company names, tickers, and legal entity identifiers\n"
            "- Financial metrics (revenue, assets, liabilities) with exact values\n"
            "- Regulatory references (GAAP, IFRS, SEC filings)\n"
            "- Temporal context (fiscal year, quarter, date)\n\n"
            "{{constraints_summary}}\n\n"
            'Return JSON with "nodes" and "relationships" keys.'
        ),
        user="Financial document:\n{{text}}",
    ),
    "legal": PromptTemplate(
        system=(
            "You are a legal domain expert.\n"
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Extract legal entities:\n{{entity_types}}\n\n"
            "Extract legal relationships:\n{{relationship_types}}\n\n"
            "Pay special attention to:\n"
            "- Parties (plaintiff, defendant, counsel)\n"
            "- Statutes, regulations, and case citations\n"
            "- Contractual obligations and clauses\n"
            "- Dates, deadlines, and jurisdictions\n\n"
            "{{constraints_summary}}\n\n"
            'Return JSON with "nodes" and "relationships" keys.'
        ),
        user="Legal document:\n{{text}}",
    ),
    "medical": PromptTemplate(
        system=(
            "You are a medical domain expert.\n"
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Extract medical entities:\n{{entity_types}}\n\n"
            "Extract medical relationships:\n{{relationship_types}}\n\n"
            "Pay special attention to:\n"
            "- Drug names (generic and brand)\n"
            "- Symptoms, conditions, and diagnoses\n"
            "- Dosages, interactions, and contraindications\n"
            "- Clinical trial identifiers and outcomes\n\n"
            "{{constraints_summary}}\n\n"
            'Return JSON with "nodes" and "relationships" keys.'
        ),
        user="Medical document:\n{{text}}",
    ),
    "research": PromptTemplate(
        system=(
            "You are an academic research extraction expert.\n"
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Extract research entities:\n{{entity_types}}\n\n"
            "Extract relationships:\n{{relationship_types}}\n\n"
            "Pay special attention to:\n"
            "- Authors, affiliations, and institutions\n"
            "- Methods, algorithms, and techniques\n"
            "- Datasets, benchmarks, and metrics with values\n"
            "- Citations and cross-references\n\n"
            "{{constraints_summary}}\n\n"
            'Return JSON with "nodes" and "relationships" keys.'
        ),
        user="Research paper:\n{{text}}",
    ),
}


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class PromptStrategy:
    """Base class for ontology-driven prompt strategies."""

    def __init__(self, ontology: Ontology) -> None:
        self.ontology = ontology

    def render(self, text: str, **kwargs: Any) -> tuple[str, str]:
        """Return (system_prompt, user_prompt)."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class ExtractionStrategy(PromptStrategy):
    """Generates prompts for entity/relationship extraction (graph indexing).

    Accepts an optional ``prompt_template`` for full customization::

        from seocho.query import ExtractionStrategy, PromptTemplate, PRESET_PROMPTS

        # Custom template
        ext = ExtractionStrategy(ontology, prompt_template=PromptTemplate(
            system="You are a FIBO expert.\\n{{entity_types}}",
        ))

        # Domain preset
        ext = ExtractionStrategy(ontology, prompt_template=PRESET_PROMPTS["finance"])

    If no template is provided, the default general-purpose prompt is used.
    """

    def __init__(
        self,
        ontology: Ontology,
        *,
        category: str = "general",
        shacl_constraints: Optional[str] = None,
        vocabulary_terms: Optional[str] = None,
        developer_instructions: Optional[str] = None,
        prompt_template: Optional[PromptTemplate] = None,
    ) -> None:
        super().__init__(ontology)
        self.category = category
        self.shacl_constraints = shacl_constraints
        self.vocabulary_terms = vocabulary_terms
        self.developer_instructions = developer_instructions
        self.prompt_template = prompt_template

    def render(self, text: str, **kwargs: Any) -> tuple[str, str]:
        ctx = self.ontology.to_extraction_context()

        # If user provided a custom template, use it
        if self.prompt_template is not None:
            return self.prompt_template.render(ctx, text)

        parts: List[str] = []

        parts.append("You are an expert entity extraction system.")
        parts.append(f'You are working with the "{ctx["ontology_name"]}" ontology.')
        parts.append("")
        parts.append("Extract entities of the following types:")
        parts.append(ctx["entity_types"])
        parts.append("")
        parts.append("Extract relationships of the following types:")
        parts.append(ctx["relationship_types"])

        if ctx.get("constraints_summary"):
            parts.append("")
            parts.append("Property constraints to respect:")
            parts.append(ctx["constraints_summary"])

        if self.shacl_constraints:
            parts.append("")
            parts.append("SHACL-like constraint hints:")
            parts.append(self.shacl_constraints)

        if self.vocabulary_terms:
            parts.append("")
            parts.append("Vocabulary / SKOS term hints for canonicalization:")
            parts.append(self.vocabulary_terms)

        if self.developer_instructions:
            parts.append("")
            parts.append("Developer instructions:")
            parts.append(self.developer_instructions)

        metadata = kwargs.get("metadata")
        if metadata:
            parts.append("")
            parts.append(f"Source metadata: {_sanitize_prompt_value(metadata)}")

        parts.append("")
        parts.append(
            'Return the output in JSON format with two keys: "nodes" and "relationships".\n'
            "\n"
            'Nodes format: {"id": "unique_id", "label": "EntityType", '
            '"properties": {"name": "Entity Name", ...}}\n'
            'Relationships format: {"source": "source_id", "target": "target_id", '
            '"type": "RELATIONSHIP_TYPE", "properties": {...}}'
        )

        system = "\n".join(parts)
        user = f"Text to extract:\n{text}"
        return system, user


# ---------------------------------------------------------------------------
# Query — the critical new capability
# ---------------------------------------------------------------------------


class QueryStrategy(PromptStrategy):
    """Generates prompts for ontology-aware graph querying.

    This is the **key missing piece** in the current architecture.  The LLM
    generating Cypher or answering natural-language questions now receives:

    - Full graph schema (node types, relationship types, property types)
    - Cardinality information (ONE_TO_MANY, etc.)
    - Constraint hints (UNIQUE → use exact match)
    - Optional live schema stats (node counts, etc.)

    This dramatically reduces hallucinated labels, wrong relationship
    directions, and invalid property access in generated Cypher.
    """

    def __init__(
        self,
        ontology: Ontology,
        *,
        schema_info: Optional[Dict[str, Any]] = None,
        vocabulary_terms: Optional[str] = None,
    ) -> None:
        super().__init__(ontology)
        self.schema_info = schema_info or {}
        self.vocabulary_terms = vocabulary_terms

    def render(self, question: str, **kwargs: Any) -> tuple[str, str]:
        ctx = self.ontology.to_query_context()
        parts: List[str] = []

        parts.append("You are a knowledge graph query agent.")
        parts.append(
            "Given a user question, generate a Cypher query that answers it. "
            "Only use node labels, relationship types, and properties "
            "defined in the schema below."
        )
        parts.append("")
        parts.append("--- Graph Schema ---")
        parts.append(ctx["graph_schema"])

        if ctx.get("query_hints"):
            parts.append("")
            parts.append("--- Query Hints ---")
            parts.append(ctx["query_hints"])

        if self.schema_info:
            parts.append("")
            parts.append("--- Live Schema Stats ---")
            for k, v in self.schema_info.items():
                parts.append(f"- {_sanitize_prompt_value(k)}: {_sanitize_prompt_value(v)}")

        if self.vocabulary_terms:
            parts.append("")
            parts.append("--- Vocabulary Aliases ---")
            parts.append(self.vocabulary_terms)

        parts.append("")
        parts.append(
            "Return a JSON object with:\n"
            '  "cypher": the Cypher query string\n'
            '  "params": dict of query parameters (use $param syntax in cypher)\n'
            '  "explanation": brief explanation of your query strategy'
        )

        system = "\n".join(parts)
        user = f"Question: {question}"
        return system, user

    def render_answer(self, question: str, cypher_result: Any, **kwargs: Any) -> tuple[str, str]:
        """Generate a prompt for synthesizing a natural-language answer
        from Cypher query results.

        Parameters
        ----------
        question:
            The original user question.
        cypher_result:
            The result set from executing the generated Cypher query.
        """
        ctx = self.ontology.to_query_context()
        parts: List[str] = []

        parts.append("You are a knowledge graph answer synthesis agent.")
        parts.append(f'Working with the "{ctx["ontology_name"]}" graph.')
        parts.append("")
        parts.append("Available node types: " + ctx["node_types"])
        parts.append("Available relationships: " + ctx["relationship_types"])
        parts.append("")
        parts.append(
            "Given the user's question and the query results below, "
            "produce a clear, factual answer. Only state facts supported "
            "by the query results. If the results are empty, say so."
        )

        system = "\n".join(parts)
        user = (
            f"Question: {question}\n\n"
            f"Query results:\n{cypher_result}"
        )
        return system, user


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------


class LinkingStrategy(PromptStrategy):
    """Generates prompts for entity deduplication and canonical ID assignment."""

    def __init__(
        self,
        ontology: Ontology,
        *,
        category: str = "general",
        vocabulary_terms: Optional[str] = None,
        developer_instructions: Optional[str] = None,
    ) -> None:
        super().__init__(ontology)
        self.category = category
        self.vocabulary_terms = vocabulary_terms
        self.developer_instructions = developer_instructions

    def render(self, entities_json: str, **kwargs: Any) -> tuple[str, str]:
        ctx = self.ontology.to_linking_context()
        parts: List[str] = []

        parts.append(
            "You are an expert entity linking system. Your goal is to "
            "identify duplicate entities and link them to a canonical ID."
        )
        parts.append("")
        parts.append(
            f"You will be provided with a list of extracted entities "
            f"from a document in the category: {self.category}."
        )
        parts.append("")
        parts.append("Task:")
        parts.append("1. Analyze entities for semantic similarity and potential duplicates.")
        parts.append('2. Assign a "linked_id" (standardized URN for well-known entities, '
                      "normalized ID for local duplicates).")
        parts.append('3. Return JSON with the same structure but with "linked_id" added.')

        if ctx.get("ontology_name"):
            parts.append("")
            parts.append(f'Ontology: {ctx["ontology_name"]}')

        if ctx.get("entity_types"):
            parts.append("")
            parts.append("Known entity types:")
            parts.append(ctx["entity_types"])

        if ctx.get("relationship_types"):
            parts.append("")
            parts.append("Relationship hints:")
            parts.append(ctx["relationship_types"])

        if self.vocabulary_terms:
            parts.append("")
            parts.append("Vocabulary hints:")
            parts.append(self.vocabulary_terms)

        if self.developer_instructions:
            parts.append("")
            parts.append("Developer instructions:")
            parts.append(self.developer_instructions)

        system = "\n".join(parts)
        user = f"Input Entities:\n{entities_json}"
        return system, user
