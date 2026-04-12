"""
Deterministic Cypher builder — assembles correct Cypher from intent + ontology.

Instead of asking the LLM to generate raw Cypher (error-prone, especially
for n10s prefixed relationships), this module:

1. LLM extracts **intent** (what entities, what relationships, what question type)
2. Code assembles **correct Cypher** using ontology metadata

This eliminates:
- Wrong namespace prefixes (schema__worksFor vs schema:worksFor)
- Missing uri property lookups
- Invalid label names
- Incorrect relationship directions (cardinality-aware)

Usage::

    from seocho.query.cypher_builder import CypherBuilder

    builder = CypherBuilder(ontology)
    cypher, params = builder.build(
        intent="relationship_lookup",
        anchor_entity="Samsung",
        anchor_label="Organization",
    )
    # → MATCH (n:Organization {name: $anchor})...
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..ontology import Ontology


class CypherBuilder:
    """Builds correct Cypher queries from structured intent.

    Supports both LPG and RDF/n10s modes based on ``ontology.graph_model``.
    """

    def __init__(self, ontology: Ontology) -> None:
        self.ontology = ontology
        self._is_rdf = ontology.graph_model in ("rdf", "hybrid")
        self._ns_prefix = self._compute_ns_prefix()

    def _compute_ns_prefix(self) -> str:
        """Compute the n10s namespace prefix for relationships."""
        ns = self.ontology.namespace
        if not ns:
            return ""
        # n10s converts "https://schema.org/" → "schema"
        # and uses it as "schema__propertyName"
        parts = ns.rstrip("/").split("/")
        last = parts[-1] if parts else ""
        # Common patterns
        if "schema.org" in ns:
            return "ns0"  # n10s default prefix for schema.org
        if last:
            return last.lower().replace(".", "_").replace("-", "_")
        return "ns0"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        *,
        intent: str,
        anchor_entity: str = "",
        anchor_label: str = "",
        target_entity: str = "",
        target_label: str = "",
        relationship_type: str = "",
        limit: int = 20,
    ) -> Tuple[str, Dict[str, Any]]:
        """Build a Cypher query from structured intent.

        Parameters
        ----------
        intent:
            Query type: "entity_lookup", "relationship_lookup",
            "neighbors", "path", "count", "list_all"
        anchor_entity:
            The main entity name to search for.
        anchor_label:
            Node label (e.g. "Person", "Organization").
        target_entity:
            Secondary entity (for relationship queries).
        target_label:
            Secondary entity label.
        relationship_type:
            Specific relationship to filter on.
        limit:
            Max result rows.

        Returns
        -------
        Tuple of (cypher_string, params_dict).
        """
        # Validate label against ontology
        if anchor_label and anchor_label not in self.ontology.nodes:
            anchor_label = ""  # fall back to unlabeled

        if intent == "entity_lookup":
            return self._entity_lookup(anchor_entity, anchor_label, limit)
        elif intent == "relationship_lookup":
            return self._relationship_lookup(
                anchor_entity, anchor_label,
                target_entity, target_label,
                relationship_type, limit,
            )
        elif intent == "neighbors":
            return self._neighbors(anchor_entity, anchor_label, limit)
        elif intent == "path":
            return self._path(anchor_entity, target_entity, limit)
        elif intent == "count":
            return self._count(anchor_label)
        elif intent == "list_all":
            return self._list_all(anchor_label, limit)
        else:
            return self._neighbors(anchor_entity, anchor_label, limit)

    # ------------------------------------------------------------------
    # Intent prompt (for LLM to extract intent only)
    # ------------------------------------------------------------------

    def intent_extraction_prompt(self) -> str:
        """System prompt for LLM intent extraction (NOT Cypher generation).

        The LLM should return JSON with intent fields, not raw Cypher.
        """
        labels = list(self.ontology.nodes.keys())
        rel_types = list(self.ontology.relationships.keys())

        return (
            "You are a question analyzer for a knowledge graph.\n"
            "Given a user question, extract the INTENT — do NOT generate Cypher.\n\n"
            f"Available node types: {', '.join(labels)}\n"
            f"Available relationship types: {', '.join(rel_types)}\n\n"
            "Return a JSON object with:\n"
            '  "intent": one of "entity_lookup", "relationship_lookup", "neighbors", "path", "count", "list_all"\n'
            '  "anchor_entity": the main entity name mentioned\n'
            '  "anchor_label": which node type it is (from the list above, or empty)\n'
            '  "target_entity": secondary entity (if asking about a relationship)\n'
            '  "target_label": secondary entity type\n'
            '  "relationship_type": specific relationship type (if mentioned)\n\n'
            "Examples:\n"
            '  "Who is the CEO of Samsung?" → {"intent": "relationship_lookup", "anchor_entity": "Samsung", "anchor_label": "Organization", "relationship_type": "CEO_OF"}\n'
            '  "Tell me about Apple" → {"intent": "neighbors", "anchor_entity": "Apple", "anchor_label": "Organization"}\n'
            '  "How many companies are there?" → {"intent": "count", "anchor_label": "Organization"}\n'
        )

    # ------------------------------------------------------------------
    # LPG query templates
    # ------------------------------------------------------------------

    def _entity_lookup(self, entity: str, label: str, limit: int) -> Tuple[str, Dict]:
        label_clause = f":{label}" if label else ""
        return (
            f"MATCH (n{label_clause})\n"
            f"WHERE toLower(coalesce(n.name, n.uri, '')) CONTAINS toLower($entity)\n"
            f"RETURN n\n"
            f"LIMIT $limit",
            {"entity": entity, "limit": limit},
        )

    def _relationship_lookup(
        self, anchor: str, anchor_label: str,
        target: str, target_label: str,
        rel_type: str, limit: int,
    ) -> Tuple[str, Dict]:
        a_label = f":{anchor_label}" if anchor_label else ""
        t_label = f":{target_label}" if target_label else ""
        rel_clause = f":{self._rel_name(rel_type)}" if rel_type else ""

        where_parts = ["toLower(coalesce(a.name, a.uri, '')) CONTAINS toLower($anchor)"]
        params: Dict[str, Any] = {"anchor": anchor, "limit": limit}

        if target:
            where_parts.append("toLower(coalesce(b.name, b.uri, '')) CONTAINS toLower($target)")
            params["target"] = target

        where = " AND ".join(where_parts)

        return (
            f"MATCH (a{a_label})-[r{rel_clause}]-(b{t_label})\n"
            f"WHERE {where}\n"
            f"RETURN coalesce(a.name, a.uri) AS source,\n"
            f"       type(r) AS relationship,\n"
            f"       coalesce(b.name, b.uri) AS target,\n"
            f"       labels(b) AS target_labels\n"
            f"LIMIT $limit",
            params,
        )

    def _neighbors(self, entity: str, label: str, limit: int) -> Tuple[str, Dict]:
        label_clause = f":{label}" if label else ""
        return (
            f"MATCH (n{label_clause})\n"
            f"WHERE toLower(coalesce(n.name, n.uri, '')) CONTAINS toLower($entity)\n"
            f"OPTIONAL MATCH (n)-[r]-(m)\n"
            f"RETURN coalesce(n.name, n.uri) AS entity,\n"
            f"       properties(n) AS properties,\n"
            f"       collect(DISTINCT {{\n"
            f"         relation: type(r),\n"
            f"         neighbor: coalesce(m.name, m.uri),\n"
            f"         neighbor_labels: labels(m)\n"
            f"       }})[0..$limit] AS neighbors\n"
            f"LIMIT 1",
            {"entity": entity, "limit": limit},
        )

    def _path(self, from_entity: str, to_entity: str, limit: int) -> Tuple[str, Dict]:
        return (
            f"MATCH path = shortestPath((a)-[*..5]-(b))\n"
            f"WHERE toLower(coalesce(a.name, a.uri, '')) CONTAINS toLower($from_e)\n"
            f"  AND toLower(coalesce(b.name, b.uri, '')) CONTAINS toLower($to_e)\n"
            f"RETURN [n IN nodes(path) | coalesce(n.name, n.uri)] AS nodes,\n"
            f"       [r IN relationships(path) | type(r)] AS relationships\n"
            f"LIMIT $limit",
            {"from_e": from_entity, "to_e": to_entity, "limit": limit},
        )

    def _count(self, label: str) -> Tuple[str, Dict]:
        label_clause = f":{label}" if label else ""
        return (
            f"MATCH (n{label_clause})\n"
            f"RETURN count(n) AS count",
            {},
        )

    def _list_all(self, label: str, limit: int) -> Tuple[str, Dict]:
        label_clause = f":{label}" if label else ""
        return (
            f"MATCH (n{label_clause})\n"
            f"RETURN coalesce(n.name, n.uri, elementId(n)) AS name, labels(n) AS labels\n"
            f"ORDER BY name\n"
            f"LIMIT $limit",
            {"limit": limit},
        )

    # ------------------------------------------------------------------
    # n10s relationship name mapping
    # ------------------------------------------------------------------

    def _rel_name(self, rel_type: str) -> str:
        """Convert ontology relationship type to actual Neo4j relationship name.

        In LPG mode: WORKS_AT → WORKS_AT (unchanged)
        In RDF/n10s mode: worksFor → ns0__worksFor (prefixed)
        """
        if not self._is_rdf:
            return rel_type

        # Check if ontology has same_as for this relationship
        rd = self.ontology.relationships.get(rel_type)
        if rd and rd.same_as:
            # schema:worksFor → ns0__worksFor
            prefix, _, local = rd.same_as.partition(":")
            if local:
                return f"{self._ns_prefix}__{local}"

        # Default: prefix the rel_type
        return f"{self._ns_prefix}__{rel_type}" if self._ns_prefix else rel_type
