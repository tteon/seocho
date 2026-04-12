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
            "Important financial extraction rules:\n"
            "- Preserve business-segment or line-item metrics as separate FinancialMetric nodes.\n"
            "- Do not collapse segment metrics into a total revenue metric.\n"
            "- When the same metric appears for multiple periods, create one metric node per period.\n"
            '- Include the period in the metric node name when needed, for example "Data and access solutions revenue 2023".\n'
            "- Keep exact numeric values and their matching year/period together.\n\n"
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

    # --- RDF-aware presets ---

    "rdf_general": PromptTemplate(
        system=(
            "You are an RDF knowledge graph extraction expert.\n"
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Extract entities as RDF resources. Every entity MUST have a URI.\n"
            "Use the namespace prefix for URIs when possible.\n\n"
            "Known entity types:\n{{entity_types}}\n\n"
            "Known relationship types:\n{{relationship_types}}\n\n"
            "Return JSON with TWO keys:\n"
            '  "nodes": [{"id": "uri:...", "label": "Type", "properties": {"uri": "...", "name": "..."}}]\n'
            '  "triples": [{"subject": "uri:...", "predicate": "rdf:type", "object": "schema:Type"},\n'
            '              {"subject": "uri:...", "predicate": "schema:name", "object": "literal value"}]\n\n'
            "Rules for RDF extraction:\n"
            "- Every entity gets a URI (use urn:entity:<normalized_name> if no standard URI)\n"
            "- Properties become separate triples (subject, predicate=property_name, object=value)\n"
            "- Relationships become triples (subject=source_uri, predicate=rel_type, object=target_uri)\n"
            "- Include rdf:type triples for all entities\n"
            "- Use sameAs property to link to standard vocabulary URIs where known\n"
        ),
        user="Text to extract as RDF triples:\n{{text}}",
    ),

    "rdf_fibo": PromptTemplate(
        system=(
            "You are a FIBO (Financial Industry Business Ontology) RDF extraction expert.\n"
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Extract financial entities as RDF resources following FIBO conventions.\n\n"
            "Known entity types:\n{{entity_types}}\n\n"
            "Known relationship types:\n{{relationship_types}}\n\n"
            "FIBO conventions:\n"
            "- Use FIBO namespace prefixes (fibo-fnd:, fibo-be:, fibo-sec:)\n"
            "- Organizations get LEI or FIGI identifiers as URIs when available\n"
            "- Financial instruments reference SEC/ISIN identifiers\n"
            "- Regulatory references link to specific regulation URIs\n\n"
            "Important financial extraction rules:\n"
            "- Preserve business-segment or line-item metrics as separate FinancialMetric resources.\n"
            "- Do not collapse segment metrics into a total revenue resource.\n"
            "- When the same metric appears for multiple periods, create one resource per period.\n"
            '- Include the period in the resource name when needed, for example "Data and access solutions revenue 2023".\n'
            "- Keep exact numeric values aligned with their period.\n\n"
            "Return JSON with:\n"
            '  "nodes": [{"id": "uri", "label": "Type", "properties": {"uri": "...", "name": "..."}}]\n'
            '  "triples": [{"subject": "uri", "predicate": "predicate", "object": "uri_or_literal"}]\n'
        ),
        user="Financial document (extract as FIBO RDF):\n{{text}}",
    ),

    # --- FinDER category-specific presets ---

    "finder_overview": PromptTemplate(
        system=(
            'You are extracting company overview information from SEC 10-K filings.\n'
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Entity types:\n{{entity_types}}\nRelationships:\n{{relationship_types}}\n\n"
            "Focus on: company structure, business segments, headquarters, subsidiaries, employee count.\n"
            '{{constraints_summary}}\nReturn JSON with "nodes" and "relationships" keys.'
        ),
        user="SEC 10-K Company Overview section:\n{{text}}",
    ),
    "finder_financials": PromptTemplate(
        system=(
            'You are extracting financial metrics from SEC 10-K filings.\n'
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Entity types:\n{{entity_types}}\nRelationships:\n{{relationship_types}}\n\n"
            "Focus on: revenue, net income, operating income, growth rates, YoY comparisons, margins.\n"
            "Extract exact numerical values with year/period context.\n"
            "Preserve segment line items such as business-unit revenue as separate metrics.\n"
            "Do not replace a segment metric with Total Revenues.\n"
            'When one metric appears in multiple years, create one metric node per year and include the year in the metric name when needed.\n'
            '{{constraints_summary}}\nReturn JSON with "nodes" and "relationships" keys.'
        ),
        user="SEC 10-K Financial data:\n{{text}}",
    ),
    "finder_financials_rdf": PromptTemplate(
        system=(
            'You are extracting SEC 10-K financial metrics as RDF resources.\n'
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Entity types:\n{{entity_types}}\nRelationships:\n{{relationship_types}}\n\n"
            "Focus on: revenue, segment revenue, net income, operating income, margins, and YoY comparisons.\n"
            "Extract exact numerical values with year/period context.\n"
            "Preserve segment line items such as business-unit revenue as separate FinancialMetric resources.\n"
            "Do not replace a segment metric with Total Revenues.\n"
            'When one metric appears in multiple years, create one metric resource per year and include the year in the metric name when needed.\n'
            '{{constraints_summary}}\nReturn JSON with "nodes" and "triples" keys.'
        ),
        user="SEC 10-K Financial data (RDF):\n{{text}}",
    ),
    "finder_footnotes": PromptTemplate(
        system=(
            'You are extracting accounting footnote details from SEC 10-K filings.\n'
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Entity types:\n{{entity_types}}\nRelationships:\n{{relationship_types}}\n\n"
            "Focus on: accounting policies, detailed disclosures, estimates, contingencies.\n"
            '{{constraints_summary}}\nReturn JSON with "nodes" and "relationships" keys.'
        ),
        user="SEC 10-K Footnotes:\n{{text}}",
    ),
    "finder_governance": PromptTemplate(
        system=(
            'You are extracting corporate governance information from SEC 10-K filings.\n'
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Entity types:\n{{entity_types}}\nRelationships:\n{{relationship_types}}\n\n"
            "Focus on: executives (name+title), board members, compensation, ownership structure.\n"
            '{{constraints_summary}}\nReturn JSON with "nodes" and "relationships" keys.'
        ),
        user="SEC 10-K Governance section:\n{{text}}",
    ),
    "finder_accounting": PromptTemplate(
        system=(
            'You are extracting accounting standards and policies from SEC 10-K filings.\n'
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Entity types:\n{{entity_types}}\nRelationships:\n{{relationship_types}}\n\n"
            "Focus on: ASC/IFRS standards, depreciation methods, capitalization policies, goodwill, impairment.\n"
            '{{constraints_summary}}\nReturn JSON with "nodes" and "relationships" keys.'
        ),
        user="SEC 10-K Accounting policies:\n{{text}}",
    ),
    "finder_legal": PromptTemplate(
        system=(
            'You are extracting legal proceedings from SEC 10-K filings.\n'
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Entity types:\n{{entity_types}}\nRelationships:\n{{relationship_types}}\n\n"
            "Focus on: lawsuits, regulatory investigations, settlements, antitrust, patent claims.\n"
            '{{constraints_summary}}\nReturn JSON with "nodes" and "relationships" keys.'
        ),
        user="SEC 10-K Legal proceedings:\n{{text}}",
    ),
    "finder_risk": PromptTemplate(
        system=(
            'You are extracting risk factors from SEC 10-K filings.\n'
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Entity types:\n{{entity_types}}\nRelationships:\n{{relationship_types}}\n\n"
            "Focus on: market risk, operational risk, regulatory risk, cybersecurity, competition.\n"
            '{{constraints_summary}}\nReturn JSON with "nodes" and "relationships" keys.'
        ),
        user="SEC 10-K Risk factors:\n{{text}}",
    ),
    "finder_shareholder": PromptTemplate(
        system=(
            'You are extracting shareholder return data from SEC 10-K filings.\n'
            'Working with the "{{ontology_name}}" ontology.\n\n'
            "Entity types:\n{{entity_types}}\nRelationships:\n{{relationship_types}}\n\n"
            "Focus on: dividends, share repurchases/buybacks, total shareholder return, stock performance.\n"
            '{{constraints_summary}}\nReturn JSON with "nodes" and "relationships" keys.'
        ),
        user="SEC 10-K Shareholder return data:\n{{text}}",
    ),
}

# Category → prompt auto-selection map
CATEGORY_PROMPT_MAP: Dict[str, str] = {
    "Company Overview": "finder_overview",
    "Financials": "finder_financials",
    "Footnotes": "finder_footnotes",
    "Governance": "finder_governance",
    "Accounting": "finder_accounting",
    "Legal": "finder_legal",
    "Risk": "finder_risk",
    "Shareholder Return": "finder_shareholder",
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

        # Auto-select category-specific prompt if available
        if self.category in CATEGORY_PROMPT_MAP:
            auto_template = PRESET_PROMPTS.get(CATEGORY_PROMPT_MAP[self.category])
            if auto_template is not None:
                return auto_template.render(ctx, text)

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
# RDF Query — n10s Cypher generation
# ---------------------------------------------------------------------------


class RDFQueryStrategy(PromptStrategy):
    """Generates n10s-aware Cypher for RDF mode queries.

    When the ontology uses ``graph_model="rdf"``, this strategy generates
    Cypher that works with n10s (neosemantics) prefixed relationships
    and URI-based node lookups.

    n10s stores RDF triples as:
    - Nodes with ``uri`` property and label = RDF class
    - Relationships with prefixed names (e.g. ``schema__worksFor``)
    """

    def __init__(
        self,
        ontology: Ontology,
        *,
        schema_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(ontology)
        self.schema_info = schema_info or {}

    def render(self, question: str, **kwargs: Any) -> tuple[str, str]:
        ctx = self.ontology.to_query_context()
        ns = self.ontology.namespace or "https://schema.org/"

        parts: List[str] = []
        parts.append("You are an RDF knowledge graph query agent using Neo4j + n10s (neosemantics).")
        parts.append("")
        parts.append("IMPORTANT n10s conventions:")
        parts.append(f"- Namespace: {ns}")
        parts.append("- Nodes have a `uri` property (globally unique identifier)")
        parts.append("- RDF classes become Neo4j labels (e.g. schema:Person → :Person)")
        parts.append("- RDF properties become Neo4j relationship types with prefix")
        parts.append("  (e.g. schema:worksFor → :schema__worksFor)")
        parts.append("- Literal properties are stored directly on nodes")
        parts.append("- Use `n10s.rdf.getNodeByUri($uri)` to look up by URI")
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

        parts.append("")
        parts.append(
            "Generate n10s-compatible Cypher. Use uri-based lookups where possible.\n"
            "For relationship matching, use the prefixed form (namespace__property).\n\n"
            "Return JSON:\n"
            '  "cypher": the Cypher query string\n'
            '  "params": dict of parameters\n'
            '  "explanation": brief explanation'
        )

        system = "\n".join(parts)
        user = f"Question: {question}"
        return system, user

    def render_answer(self, question: str, cypher_result: Any, **kwargs: Any) -> tuple[str, str]:
        ctx = self.ontology.to_query_context()
        parts: List[str] = []
        parts.append("You are an RDF knowledge graph answer agent.")
        parts.append(f'Working with the "{ctx["ontology_name"]}" RDF graph.')
        parts.append("Node types: " + ctx["node_types"])
        parts.append("Produce a clear factual answer from the query results.")

        system = "\n".join(parts)
        user = f"Question: {question}\n\nQuery results:\n{cypher_result}"
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
