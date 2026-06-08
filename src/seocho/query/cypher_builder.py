"""
Deterministic Cypher builder — assembles correct Cypher from intent + ontology.

Instead of asking the LLM to generate raw Cypher (error-prone, especially
for n10s prefixed relationships), this module:

1. LLM extracts intent and entities from the question
2. Code assembles constrained Cypher from ontology metadata

This keeps query execution deterministic while still allowing the model to
classify the user question.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..cypher_ident import quote_identifier
from ..ontology import Ontology

_ENTITY_SUFFIXES = re.compile(
    r"\s*\b(Inc\.?|Corp\.?|Corporation|LLC|Ltd\.?|Co\.?|Company|Group|Holdings?|"
    r"Incorporated|Plc\.?|AG|SA|SE|GmbH|N\.?V\.?|& Co\.?)\s*$",
    re.IGNORECASE,
)
_FOUR_DIGIT_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_YEAR_RANGE_RE = re.compile(r"\b(20\d{2})\s*[-/]\s*(\d{2,4})\b")
_FINANCE_DELTA_RE = re.compile(
    r"\b(delta|difference|change|increase|decrease|grew|growth|decline|improved)\b",
    re.IGNORECASE,
)
_FINANCE_METRIC_TERMS: Dict[str, List[str]] = {
    "revenue": ["revenue", "revenues", "rev", "sales", "turnover"],
    "operating income": ["operating income", "operating profit"],
    "net income": ["net income", "earnings"],
    "income": ["income"],
    "expense": ["expense", "expenses", "cost", "costs"],
    "margin": ["margin", "margins"],
    "vehicle deliveries": [
        "vehicle deliveries",
        "vehicle delivery",
        "vehicles delivered",
        "vehicle delivered",
        "delivered",
        "deliver",
        "deliveries",
        "delivery",
    ],
    "assets": ["asset", "assets"],
    "liabilities": ["liability", "liabilities"],
    "cash flow": ["cash flow", "cashflow"],
}
_METRIC_TOKEN_STOPWORDS = {
    "delta", "difference", "change", "from", "to", "between", "compare", "comparison",
    "what", "was", "is", "the", "of", "in", "for", "did", "does", "how", "much",
    "many", "by", "show", "tell", "me", "and", "or", "fiscal", "year", "years",
    "vs", "versus", "compared", "prior", "previous",
    # Explanation-style finance prompts should not turn causal wording into
    # hard metric-name filters.
    "drive", "drives", "drove", "driven", "cause", "caused", "causes",
    "expand", "expanded", "expansion", "explain", "explained", "reason", "reasons",
}
_GENERIC_METRIC_TOKENS = {
    "revenue", "revenues", "rev", "income", "profit", "expense", "expenses",
    "cost", "costs", "margin", "margins", "assets", "liabilities", "cash", "flow",
}
_SCHEMA_HINT_STOPWORDS = {
    "what", "which", "who", "whom", "where", "when", "why", "how", "tell", "show", "find",
    "about", "with", "from", "into", "onto", "than", "then", "that", "this", "those",
    "does", "did", "were", "was", "are", "and", "for", "the", "all", "any", "many",
    "list", "count", "lookup", "query", "graph", "database", "neo4j",
}


def normalize_entity(name: str) -> str:
    """Normalize an entity name for fuzzy matching."""
    text = name.strip()
    text = text.replace("\u2019s", "").replace("'s", "")
    text = _ENTITY_SUFFIXES.sub("", text).strip()
    text = re.sub(r"\s*&\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class CypherBuilder:
    """Build correct Cypher queries from structured intent."""

    def __init__(self, ontology: Ontology) -> None:
        self.ontology = ontology
        self._is_rdf = ontology.graph_model in ("rdf", "hybrid")
        self._ns_prefix = self._compute_ns_prefix()

    def _compute_ns_prefix(self) -> str:
        ns = self.ontology.namespace
        if not ns:
            return ""
        parts = ns.rstrip("/").split("/")
        last = parts[-1] if parts else ""
        if "schema.org" in ns:
            return "ns0"
        if last:
            return last.lower().replace(".", "_").replace("-", "_")
        return "ns0"

    def build(
        self,
        *,
        intent: str,
        anchor_entity: str = "",
        anchor_label: str = "",
        target_entity: str = "",
        target_label: str = "",
        relationship_type: str = "",
        metric_name: str = "",
        metric_aliases: Optional[Sequence[str]] = None,
        metric_scope_tokens: Optional[Sequence[str]] = None,
        years: Optional[Sequence[str]] = None,
        workspace_id: str = "",
        limit: int = 20,
        schema_hints: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        hint_payload = dict(schema_hints or {})
        if anchor_label and anchor_label not in self.ontology.nodes:
            anchor_label = ""
        if target_label and target_label not in self.ontology.nodes:
            target_label = ""

        anchor_label = self._resolve_hint_label(
            current=anchor_label,
            hinted=hint_payload.get("anchor_label"),
            candidates=hint_payload.get("label_candidates", []),
        )
        target_label = self._resolve_hint_label(
            current=target_label,
            hinted=hint_payload.get("target_label"),
            candidates=hint_payload.get("label_candidates", []),
            exclude={anchor_label} if anchor_label else set(),
        )

        hinted_relationship = str(hint_payload.get("relationship_type", "")).strip()
        relationship_candidates = hint_payload.get("relationship_candidates", [])
        if not relationship_type:
            if hinted_relationship:
                relationship_type = hinted_relationship
            elif isinstance(relationship_candidates, list) and relationship_candidates:
                relationship_type = str(relationship_candidates[0]).strip()

        if anchor_label and anchor_label not in self.ontology.nodes:
            anchor_label = ""
        if relationship_type and relationship_type not in self.ontology.relationships:
            relationship_type = self._match_relationship(
                relationship_type,
                anchor_label=anchor_label,
                target_label=target_label,
            )

        # ADR-0097 G3: dispatch via externalized PatternSpec catalog.
        # Behavior is bit-identical to the pre-G3 inline if/elif chain;
        # G2 will widen this to enumerate K candidates and cost-rank.
        from . import pattern_catalog

        spec = pattern_catalog.get_by_cypher_shape(intent)
        if spec is None:
            spec = pattern_catalog.get_by_cypher_shape("neighbors")
            assert spec is not None, "neighbors fallback pattern must be registered"
        return spec.template_factory(
            self,
            intent=intent,
            anchor_entity=anchor_entity,
            anchor_label=anchor_label,
            target_entity=target_entity,
            target_label=target_label,
            relationship_type=relationship_type,
            metric_name=metric_name,
            metric_aliases=metric_aliases,
            metric_scope_tokens=metric_scope_tokens,
            years=years,
            workspace_id=workspace_id,
            limit=limit,
        )

    def normalize_intent(self, question: str, raw_intent: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Coerce an LLM intent payload into a safer structured form."""
        intent_data = dict(raw_intent or {})
        raw_intent_name = str(intent_data.get("intent", "")).strip()
        years = self._extract_years(question, intent_data.get("years"))
        anchor_entity = str(intent_data.get("anchor_entity") or "").strip()
        metric_name = str(intent_data.get("metric_name") or "").strip()
        if not metric_name:
            metric_name = self._extract_metric_phrase(question)
        metric_aliases = self._metric_aliases(metric_name or question)
        metric_scope_tokens = self._metric_scope_tokens(
            metric_name or question,
            metric_aliases=metric_aliases,
            anchor_entity=anchor_entity,
        )

        if self._is_financial_delta_question(question, raw_intent_name, years, metric_aliases):
            intent = "financial_metric_delta"
        elif self._is_financial_metric_question(question, raw_intent_name, years, metric_aliases):
            intent = "financial_metric_lookup"
        elif self._is_legal_issue_question(question, raw_intent_name):
            intent = "relationship_lookup"
        else:
            intent = raw_intent_name or "neighbors"

        if intent in {"financial_metric_lookup", "financial_metric_delta"}:
            intent_data["anchor_label"] = str(intent_data.get("anchor_label") or "Company")
            intent_data["target_label"] = str(intent_data.get("target_label") or "FinancialMetric")
        elif intent == "relationship_lookup" and self._is_legal_issue_question(question, raw_intent_name):
            anchor_label = str(intent_data.get("anchor_label") or "Company")
            target_label = str(intent_data.get("target_label") or "LegalIssue")
            intent_data["anchor_label"] = anchor_label
            intent_data["target_label"] = target_label
            if not str(intent_data.get("relationship_type") or "").strip():
                intent_data["relationship_type"] = self._match_relationship(
                    "INVOLVED_IN",
                    anchor_label=anchor_label,
                    target_label=target_label,
                )

        intent_data["intent"] = intent
        intent_data["metric_name"] = metric_name
        intent_data["metric_aliases"] = metric_aliases
        intent_data["metric_scope_tokens"] = metric_scope_tokens
        intent_data["years"] = years
        return intent_data

    def intent_extraction_prompt(self, *, schema_hints: Optional[Dict[str, Any]] = None) -> str:
        profile = self.ontology.to_query_profile()
        labels = list(self.ontology.nodes.keys())
        rel_descriptions = []
        for rtype, rd in self.ontology.relationships.items():
            desc = rd.description or rtype
            rel_descriptions.append(f"  - {rtype}: ({rd.source})→({rd.target}) — {desc}")
        rel_block = "\n".join(rel_descriptions) if rel_descriptions else "  (none defined)"

        node_descriptions = []
        for label, nd in self.ontology.nodes.items():
            props = ", ".join(nd.properties.keys())
            desc = nd.description or label
            node_descriptions.append(f"  - {label}: {desc} (properties: {props})")
        node_block = "\n".join(node_descriptions)
        hint_block = self.render_schema_hints(schema_hints)
        hint_prefix = f"Question-scoped schema hints:\n{hint_block}\n\n" if hint_block else ""

        return (
            "You are a question analyzer for a knowledge graph.\n"
            "\n"
            "Task:\n"
            "- Extract the question intent and the minimal structured fields needed for deterministic query planning.\n"
            "- Do NOT generate Cypher.\n\n"
            "Context:\n"
            "- The graph was built using the ontology below.\n"
            "- You MUST use ONLY the node types and relationship types listed here.\n"
            f"- Ontology query profile: package_id={profile['package_id']}, "
            f"version={profile['version']}, graph_model={profile['graph_model']}.\n"
            f"- Deterministic intents supported: {', '.join(profile['deterministic_intents'])}.\n\n"
            f"{hint_prefix}"
            f"Node types:\n{node_block}\n\n"
            f"Relationship types (ONLY these exist in the graph):\n{rel_block}\n\n"
            "Constraints:\n"
            "- Do NOT invent new node or relationship types.\n"
            "- If the question implies a relationship not in the list, use the closest supported relationship or set relationship_type to empty.\n"
            "- Keep entity strings close to the original user wording.\n\n"
            "Output format:\n"
            "- Return exactly one valid json object with:\n"
            '  "intent": one of "entity_lookup", "relationship_lookup", "neighbors", "path", "count", "list_all", "financial_metric_lookup", "financial_metric_delta"\n'
            '  "anchor_entity": the main entity name mentioned\n'
            f'  "anchor_label": one of [{", ".join(labels)}] or empty\n'
            '  "target_entity": secondary entity (if asking about a relationship)\n'
            '  "target_label": secondary entity type\n'
            f'  "relationship_type": one of [{", ".join(self.ontology.relationships.keys())}] or empty\n'
            '  "metric_name": financial metric or line-item phrase when asking about a metric value or delta\n'
            '  "years": list of years mentioned in the question\n\n'
            "Verification:\n"
            "- Before finalizing, check that the json is valid.\n"
            "- Check that labels and relationship types are from the allowed ontology lists.\n"
            "- Check that empty fields stay empty instead of being guessed.\n\n"
            "Examples with this ontology:\n"
            '  "Who works at Samsung?" → {"intent": "relationship_lookup", "anchor_entity": "Samsung", "anchor_label": "Company", "relationship_type": "EMPLOYS"}\n'
            '  "Tell me about Apple" → {"intent": "neighbors", "anchor_entity": "Apple", "anchor_label": "Company"}\n'
            '  "How many companies?" → {"intent": "count", "anchor_label": "Company"}\n'
            '  "Delta in CBOE Data & Access Solutions rev from 2021-23." → {"intent": "financial_metric_delta", "anchor_entity": "CBOE", "anchor_label": "Company", "metric_name": "Data & Access Solutions revenue", "years": ["2021", "2023"]}\n'
        )

    def derive_schema_hints(
        self,
        question: str,
        *,
        raw_intent: Optional[Dict[str, Any]] = None,
        resolved_entities: Sequence[str] = (),
        label_hints: Sequence[str] = (),
    ) -> Dict[str, Any]:
        intent_data = dict(raw_intent or {})
        raw_texts: List[str] = [question]
        raw_texts.extend(str(item) for item in resolved_entities if str(item).strip())
        for key in (
            "intent",
            "anchor_entity",
            "anchor_label",
            "target_entity",
            "target_label",
            "relationship_type",
            "metric_name",
        ):
            value = str(intent_data.get(key, "") or "").strip()
            if value:
                raw_texts.append(value)
        raw_texts.extend(str(item) for item in label_hints if str(item).strip())

        normalized_blob = " ".join(self._normalize_hint_text(text) for text in raw_texts if str(text).strip())
        topic_terms: List[str] = []
        for text in raw_texts:
            for token in re.findall(r"[a-z][a-z0-9_]+", str(text).lower().replace("&", " and ")):
                if token in _SCHEMA_HINT_STOPWORDS or token in _METRIC_TOKEN_STOPWORDS:
                    continue
                if token not in topic_terms:
                    topic_terms.append(token)

        label_scores: Dict[str, int] = {}
        property_candidates: List[str] = []
        for label, node_def in self.ontology.nodes.items():
            score = self._hint_match_score(normalized_blob, [label, *node_def.aliases])
            for property_name, prop in node_def.properties.items():
                property_score = self._hint_match_score(
                    normalized_blob,
                    [property_name, *prop.aliases],
                )
                if property_score > 0:
                    score += property_score
                    property_key = f"{label}.{property_name}"
                    if property_key not in property_candidates:
                        property_candidates.append(property_key)
            if score > 0:
                label_scores[label] = score

        relationship_scores: Dict[str, int] = {}
        for rel_name, rel_def in self.ontology.relationships.items():
            score = self._hint_match_score(
                normalized_blob,
                [rel_name, *rel_def.aliases, rel_def.description],
            )
            if rel_def.source in label_scores:
                score += 1
            if rel_def.target in label_scores:
                score += 1
            if score > 0:
                relationship_scores[rel_name] = score

        label_candidates = [
            label
            for label, _ in sorted(label_scores.items(), key=lambda item: (-item[1], item[0]))
        ]
        relationship_candidates = [
            rel_name
            for rel_name, _ in sorted(relationship_scores.items(), key=lambda item: (-item[1], item[0]))
        ]

        raw_anchor_label = str(intent_data.get("anchor_label", "") or "").strip()
        raw_target_label = str(intent_data.get("target_label", "") or "").strip()
        anchor_label = raw_anchor_label if raw_anchor_label in self.ontology.nodes else ""
        target_label = raw_target_label if raw_target_label in self.ontology.nodes else ""
        if not anchor_label and label_candidates:
            anchor_label = label_candidates[0]
        if not target_label:
            for candidate in label_candidates:
                if candidate != anchor_label:
                    target_label = candidate
                    break

        relationship_type = str(intent_data.get("relationship_type", "") or "").strip()
        if relationship_type and relationship_type not in self.ontology.relationships:
            relationship_type = self._match_relationship(
                relationship_type,
                anchor_label=anchor_label,
                target_label=target_label,
            )
        if not relationship_type and relationship_candidates:
            relationship_type = self._match_relationship(
                relationship_candidates[0],
                anchor_label=anchor_label,
                target_label=target_label,
            ) or relationship_candidates[0]

        return {
            "namespace": self.ontology.namespace,
            "ontology_package_id": self.ontology.package_id,
            "ontology_version": self.ontology.version,
            "topic_terms": topic_terms[:12],
            "label_candidates": label_candidates[:6],
            "relationship_candidates": relationship_candidates[:6],
            "property_candidates": property_candidates[:10],
            "anchor_label": anchor_label,
            "target_label": target_label,
            "relationship_type": relationship_type,
        }

    def render_schema_hints(self, schema_hints: Optional[Dict[str, Any]]) -> str:
        hints = dict(schema_hints or {})
        if not hints:
            return ""
        lines: List[str] = []
        namespace = str(hints.get("namespace", "")).strip()
        if namespace:
            lines.append(f"- Namespace: {namespace}")
        topic_terms = hints.get("topic_terms", [])
        if isinstance(topic_terms, list) and topic_terms:
            lines.append(f"- Topic terms: {', '.join(str(item) for item in topic_terms)}")
        label_candidates = hints.get("label_candidates", [])
        if isinstance(label_candidates, list) and label_candidates:
            lines.append(f"- Candidate labels: {', '.join(str(item) for item in label_candidates)}")
        relationship_candidates = hints.get("relationship_candidates", [])
        if isinstance(relationship_candidates, list) and relationship_candidates:
            lines.append(
                f"- Candidate relationships: {', '.join(str(item) for item in relationship_candidates)}"
            )
        property_candidates = hints.get("property_candidates", [])
        if isinstance(property_candidates, list) and property_candidates:
            lines.append(f"- Candidate properties: {', '.join(str(item) for item in property_candidates)}")
        anchor_label = str(hints.get("anchor_label", "")).strip()
        target_label = str(hints.get("target_label", "")).strip()
        relationship_type = str(hints.get("relationship_type", "")).strip()
        if anchor_label:
            lines.append(f"- Preferred anchor label: {anchor_label}")
        if target_label:
            lines.append(f"- Preferred target label: {target_label}")
        if relationship_type:
            lines.append(f"- Preferred relationship: {relationship_type}")
        return "\n".join(lines)

    def _entity_lookup(self, entity: str, label: str, workspace_id: str, limit: int) -> Tuple[str, Dict[str, Any]]:
        label_clause = f":{quote_identifier(label)}" if label else ""
        normalized = normalize_entity(entity)
        return (
            f"MATCH (n{label_clause})\n"
            "WHERE (toLower(coalesce(n.name, n.uri, '')) CONTAINS toLower($entity)\n"
            "   OR toLower(coalesce(n.name, n.uri, '')) CONTAINS toLower($entity_norm))\n"
            "  AND ($workspace_id = '' OR coalesce(n._workspace_id, '') = $workspace_id)\n"
            "RETURN n\n"
            "LIMIT $limit",
            {
                "entity": entity,
                "entity_norm": normalized,
                "workspace_id": workspace_id,
                "limit": limit,
            },
        )

    def _relationship_lookup(
        self,
        anchor: str,
        anchor_label: str,
        target: str,
        target_label: str,
        rel_type: str,
        workspace_id: str,
        limit: int,
    ) -> Tuple[str, Dict[str, Any]]:
        a_label = f":{quote_identifier(anchor_label)}" if anchor_label else ""
        t_label = f":{quote_identifier(target_label)}" if target_label else ""
        rel_clause = f":{quote_identifier(self._rel_name(rel_type))}" if rel_type else ""

        anchor_norm = normalize_entity(anchor)
        where_parts = [
            "(toLower(coalesce(a.name, a.uri, '')) CONTAINS toLower($anchor) "
            "OR toLower(coalesce(a.name, a.uri, '')) CONTAINS toLower($anchor_norm))",
            "($workspace_id = '' OR (coalesce(a._workspace_id, '') = $workspace_id AND coalesce(b._workspace_id, '') = $workspace_id))",
        ]
        params: Dict[str, Any] = {
            "anchor": anchor,
            "anchor_norm": anchor_norm,
            "workspace_id": workspace_id,
            "limit": limit,
        }

        if target:
            target_norm = normalize_entity(target)
            where_parts.append(
                "(toLower(coalesce(b.name, b.uri, '')) CONTAINS toLower($target) "
                "OR toLower(coalesce(b.name, b.uri, '')) CONTAINS toLower($target_norm))"
            )
            params["target"] = target
            params["target_norm"] = target_norm

        where = " AND ".join(where_parts)
        return (
            f"MATCH (a{a_label})-[r{rel_clause}]-(b{t_label})\n"
            f"WHERE {where}\n"
            "RETURN coalesce(a.name, a.uri) AS source,\n"
            "       type(r) AS relationship,\n"
            "       coalesce(b.name, b.uri) AS target,\n"
            "       labels(b) AS target_labels,\n"
            "       properties(b) AS target_properties,\n"
            "       coalesce(b.content_preview, b.description, b.content, '') AS supporting_fact\n"
            "LIMIT $limit",
            params,
        )

    def _neighbors(self, entity: str, label: str, workspace_id: str, limit: int) -> Tuple[str, Dict[str, Any]]:
        label_clause = f":{quote_identifier(label)}" if label else ""
        normalized = normalize_entity(entity)
        return (
            f"MATCH (n{label_clause})\n"
            "WHERE (toLower(coalesce(n.name, n.uri, '')) CONTAINS toLower($entity)\n"
            "   OR toLower(coalesce(n.name, n.uri, '')) CONTAINS toLower($entity_norm))\n"
            "  AND ($workspace_id = '' OR coalesce(n._workspace_id, '') = $workspace_id)\n"
            "OPTIONAL MATCH (n)-[r]-(m)\n"
            "WHERE $workspace_id = '' OR coalesce(m._workspace_id, '') = $workspace_id\n"
            "RETURN coalesce(n.name, n.uri) AS entity,\n"
            "       properties(n) AS properties,\n"
            "       collect(DISTINCT {\n"
            "         relation: type(r),\n"
            "         neighbor: coalesce(m.name, m.uri),\n"
            "         neighbor_labels: labels(m)\n"
            "       })[0..$limit] AS neighbors,\n"
            "       coalesce(n.content_preview, n.description, n.content, '') AS supporting_fact\n"
            "LIMIT 1",
            {
                "entity": entity,
                "entity_norm": normalized,
                "workspace_id": workspace_id,
                "limit": limit,
            },
        )

    def _path(self, from_entity: str, to_entity: str, workspace_id: str, limit: int) -> Tuple[str, Dict[str, Any]]:
        return (
            "MATCH path = shortestPath((a)-[*..5]-(b))\n"
            "WHERE toLower(coalesce(a.name, a.uri, '')) CONTAINS toLower($from_e)\n"
            "  AND toLower(coalesce(b.name, b.uri, '')) CONTAINS toLower($to_e)\n"
            "  AND ($workspace_id = '' OR (coalesce(a._workspace_id, '') = $workspace_id AND coalesce(b._workspace_id, '') = $workspace_id))\n"
            "RETURN [n IN nodes(path) | coalesce(n.name, n.uri)] AS nodes,\n"
            "       [r IN relationships(path) | type(r)] AS relationships\n"
            "LIMIT $limit",
            {
                "from_e": from_entity,
                "to_e": to_entity,
                "workspace_id": workspace_id,
                "limit": limit,
            },
        )

    def _count(self, label: str, workspace_id: str) -> Tuple[str, Dict[str, Any]]:
        label_clause = f":{quote_identifier(label)}" if label else ""
        return (
            f"MATCH (n{label_clause})\n"
            "WHERE $workspace_id = '' OR coalesce(n._workspace_id, '') = $workspace_id\n"
            "RETURN count(n) AS count",
            {"workspace_id": workspace_id},
        )

    def _list_all(self, label: str, workspace_id: str, limit: int) -> Tuple[str, Dict[str, Any]]:
        label_clause = f":{quote_identifier(label)}" if label else ""
        return (
            f"MATCH (n{label_clause})\n"
            "WHERE $workspace_id = '' OR coalesce(n._workspace_id, '') = $workspace_id\n"
            "RETURN coalesce(n.name, n.uri, elementId(n)) AS name, labels(n) AS labels\n"
            "ORDER BY name\n"
            "LIMIT $limit",
            {"workspace_id": workspace_id, "limit": limit},
        )

    def _metric_anchor_labels(self) -> Tuple[List[str], List[str]]:
        """Derive (metric_labels, anchor_labels) from the active ontology.

        Ontology-aware so FIBO graphs (``LegalEntity`` reporting ``Revenue`` /
        ``NetIncome`` / ``EPS`` … subclasses) are matched instead of a hardcoded
        ``Company`` / ``FinancialMetric`` schema. The same derivation runs for
        every ontology arm, so the comparison stays fair.

        - metric_labels: ontology node labels carrying a ``value`` property
          (the concrete financial-figure classes), plus the canonical bases for
          backward compatibility.
        - anchor_labels: source labels of relationships whose target is a metric
          label (e.g. ``LegalEntity`` via ``REPORTED_METRIC``), plus legacy
          aliases. Anchor matching is permissive (name-contains does the real
          work), so an empty/looser set never blocks retrieval.
        """
        metric_labels: List[str] = []
        for label, nd in self.ontology.nodes.items():
            props = getattr(nd, "properties", {}) or {}
            if isinstance(props, dict) and any(str(k).lower() == "value" for k in props):
                metric_labels.append(label)
        for legacy in ("FinancialMetric", "MonetaryAmount"):
            if legacy not in metric_labels:
                metric_labels.append(legacy)
        metric_set = set(metric_labels)
        anchor_labels = sorted({
            rd.source
            for rd in self.ontology.relationships.values()
            if getattr(rd, "target", None) in metric_set and rd.source and rd.source != "Any"
        })
        for legacy in ("Company", "LegalEntity", "Entity"):
            if legacy not in anchor_labels:
                anchor_labels.append(legacy)
        return metric_labels, anchor_labels

    def _financial_metric_lookup(
        self,
        *,
        anchor_entity: str,
        metric_name: str,
        metric_aliases: Sequence[str],
        metric_scope_tokens: Sequence[str],
        years: Sequence[str],
        workspace_id: str,
        limit: int,
    ) -> Tuple[str, Dict[str, Any]]:
        metric_labels, anchor_labels = self._metric_anchor_labels()
        # Labels are passed as parameters and matched via `l IN $list` — no
        # dynamic label interpolation into Cypher (CLAUDE.md §8). Read-only.
        return (
            "MATCH (c)-[r]-(m)\n"
            "WHERE (ANY(l IN labels(m) WHERE l IN $metric_labels) OR m.value IS NOT NULL)\n"
            "  AND ($anchor_labels = [] OR ANY(l IN labels(c) WHERE l IN $anchor_labels))\n"
            # Anchor by company name OR by ticker symbol — FinDER questions often
            # use the ticker ("UR", "JKHY") while extracted nodes carry the full
            # name ("United Rentals, Inc."). Parameterized, read-only (§8).
            "  AND (toLower(coalesce(c.name, c.uri, '')) CONTAINS toLower($anchor)\n"
            "   OR toLower(coalesce(c.name, c.uri, '')) CONTAINS toLower($anchor_norm)\n"
            "   OR toLower(coalesce(c.ticker, '')) = toLower($anchor)\n"
            "   OR toLower(coalesce(c.ticker, '')) = toLower($anchor_norm))\n"
            "  AND ($workspace_id = '' OR (coalesce(c._workspace_id, '') = $workspace_id AND coalesce(m._workspace_id, '') = $workspace_id))\n"
            "  AND ($years = [] OR ANY(year IN $years WHERE coalesce(toString(m.year), '') = year\n"
            "        OR toLower(coalesce(toString(m.period), '')) CONTAINS year\n"
            "        OR toLower(coalesce(m.name, m.uri, '')) CONTAINS year))\n"
            # metric_aliases / metric_scope_tokens are used only as SOFT ranking
            # signals, never as hard filters. They are derived heuristically from
            # the question (often question stopwords like 'trend'/'prod'), and an
            # ALL/ANY hard filter on them eliminated every metric node even when
            # the answer data was present. The per-entity metric set is small, so
            # we return the anchor's metrics and let the LLM select; alias/token
            # matches just float to the top. (CLAUDE.md §8: read-only.)
            "RETURN coalesce(c.name, c.uri) AS company,\n"
            "       coalesce(m.name, m.uri) AS metric_name,\n"
            "       coalesce(toString(m.year), toString(m.period), '') AS year,\n"
            "       CASE WHEN m.value IS NULL THEN '' ELSE toString(m.value) END AS value,\n"
            "       type(r) AS relationship,\n"
            "       coalesce(m.content_preview, c.content_preview, m.description, c.description, '') AS supporting_fact\n"
            "ORDER BY\n"
            "  CASE WHEN ($metric_aliases = [] OR ANY(alias IN $metric_aliases WHERE toLower(coalesce(m.name, m.uri, '')) CONTAINS alias))\n"
            "         OR ($metric_scope_tokens = [] OR ANY(token IN $metric_scope_tokens WHERE toLower(coalesce(m.name, m.uri, '')) CONTAINS token))\n"
            "       THEN 0 ELSE 1 END,\n"
            "  company, year, metric_name\n"
            "LIMIT $limit",
            {
                "anchor": anchor_entity,
                "anchor_norm": normalize_entity(anchor_entity),
                "metric_name": metric_name,
                "metric_aliases": [alias.lower() for alias in metric_aliases if alias],
                "metric_scope_tokens": [token.lower() for token in metric_scope_tokens if token],
                "years": [str(year) for year in years if str(year).strip()],
                "metric_labels": metric_labels,
                "anchor_labels": anchor_labels,
                "workspace_id": workspace_id,
                "limit": limit,
            },
        )

    def _rel_name(self, rel_type: str) -> str:
        if not self._is_rdf:
            return rel_type

        rel_def = self.ontology.relationships.get(rel_type)
        if rel_def and rel_def.same_as:
            _, _, local = rel_def.same_as.partition(":")
            if local:
                return f"{self._ns_prefix}__{local}"
        return f"{self._ns_prefix}__{rel_type}" if self._ns_prefix else rel_type

    def _match_relationship(self, rel_type: str, *, anchor_label: str, target_label: str) -> str:
        # 1. Exact or alias match
        rel_lower = rel_type.lower()
        for candidate, rel_def in self.ontology.relationships.items():
            aliases = [candidate.lower(), *(alias.lower() for alias in rel_def.aliases)]
            if rel_def.same_as:
                aliases.append(rel_def.same_as.lower())
            if rel_lower not in aliases:
                continue
            if anchor_label and rel_def.source not in {"Any", anchor_label}:
                continue
            if target_label and rel_def.target not in {"Any", target_label}:
                continue
            return candidate

        # 1.5 Scored ontology grounding (icml fibo_ground port, opt-in via
        # SEOCHO_ONTOLOGY_GROUNDING): no exact/alias hit → ground rel_type
        # semantically to the closest ontology relationship above threshold,
        # respecting label compatibility. Bridges "manages" → "LED_BY".
        grounded = self._grounded_relationship(
            rel_type, anchor_label=anchor_label, target_label=target_label
        )
        if grounded:
            return grounded

        # 2. Fallback: match by source→target label compatibility
        scored: List[tuple] = []
        for candidate, rel_def in self.ontology.relationships.items():
            score = 0
            if anchor_label and rel_def.source == anchor_label:
                score += 1
            if target_label and rel_def.target == target_label:
                score += 1
            if score > 0:
                scored.append((candidate, score))
        if scored:
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[0][0]

        # 3. Last resort: if only one relationship exists, use it
        if len(self.ontology.relationships) == 1:
            return list(self.ontology.relationships.keys())[0]

        return ""

    @staticmethod
    def _ontology_grounding_enabled() -> bool:
        """Scored ontology grounding — DEFAULT OFF (opt-in via
        SEOCHO_ONTOLOGY_GROUNDING) pending its FinDER A/B."""
        import os

        return str(os.environ.get("SEOCHO_ONTOLOGY_GROUNDING", "")).strip().lower() in ("1", "true", "yes")

    def _grounding_scorer(self):
        """Resolve the grounding scorer + threshold from
        SEOCHO_GROUNDING_SCORER (default "lexical"; "embedding" uses
        fastembed, falling back to lexical if unavailable). Cached per
        builder so the embedder isn't rebuilt per call. Returns
        ``(scorer_or_None, threshold)`` — None scorer ⇒ lexical default."""
        import os

        if getattr(self, "_grounding_scorer_cache", "unset") != "unset":
            return self._grounding_scorer_cache
        mode = str(os.environ.get("SEOCHO_GROUNDING_SCORER", "lexical")).strip().lower()
        scorer, threshold = None, 0.4  # lexical default
        if mode == "embedding":
            from .embedding_grounding import make_fastembed_scorer

            emb = make_fastembed_scorer()
            if emb is not None:
                # bge cosine has a high baseline (~0.5 for unrelated), so
                # require a stronger match than the lexical threshold.
                scorer, threshold = emb, 0.55
        self._grounding_scorer_cache = (scorer, threshold)
        return self._grounding_scorer_cache

    def _grounded_relationship(self, rel_type: str, *, anchor_label: str, target_label: str) -> str:
        """Semantic grounding of rel_type to an ontology relationship.

        Returns "" when grounding is disabled, the intent is empty, or no
        candidate clears the threshold + label compatibility — so the
        caller falls through to the existing structural fallbacks.
        """
        if not rel_type or not self._ontology_grounding_enabled():
            return ""
        from .ontology_grounding import ground_edge_type

        scorer, threshold = self._grounding_scorer()
        for canon, _score in ground_edge_type(
            rel_type, self.ontology, top_k=3, threshold=threshold, scorer=scorer
        ):
            rel_def = self.ontology.relationships.get(canon)
            if rel_def is None:
                continue
            if anchor_label and rel_def.source not in {"Any", anchor_label}:
                continue
            if target_label and rel_def.target not in {"Any", target_label}:
                continue
            return canon
        return ""

    def _resolve_hint_label(
        self,
        *,
        current: str,
        hinted: Any,
        candidates: Sequence[Any],
        exclude: Optional[Set[str]] = None,
    ) -> str:
        if current in self.ontology.nodes:
            return current
        excluded = set(exclude or set())
        for candidate in [hinted, *list(candidates)]:
            label = str(candidate or "").strip()
            if label in self.ontology.nodes and label not in excluded:
                return label
        return ""

    def _relationship_candidates(self, *, source_label: str, target_label: str) -> List[str]:
        candidates: List[str] = []
        for rel_name, rel_def in self.ontology.relationships.items():
            if rel_def.source != source_label or rel_def.target != target_label:
                continue
            values = {rel_name, rel_name.upper(), rel_name.lower(), self._rel_name(rel_name)}
            if rel_def.same_as:
                _, _, local = rel_def.same_as.partition(":")
                if local:
                    values.update({local, local.lower(), local.upper()})
            for value in values:
                if value and value not in candidates:
                    candidates.append(value)
        return candidates

    @staticmethod
    def _normalize_hint_text(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()

    def _hint_match_score(self, normalized_blob: str, candidates: Sequence[Any]) -> int:
        score = 0
        for candidate in candidates:
            normalized = self._normalize_hint_text(str(candidate or ""))
            if normalized and normalized in normalized_blob:
                score += 1
        return score

    def _extract_years(self, question: str, raw_years: Any) -> List[str]:
        years: List[str] = []
        if isinstance(raw_years, (list, tuple)):
            years.extend(str(item).strip() for item in raw_years if str(item).strip())
        elif raw_years:
            years.append(str(raw_years).strip())

        for match in _YEAR_RANGE_RE.finditer(question):
            start_year = match.group(1)
            end_year = match.group(2)
            if len(end_year) == 2:
                end_year = f"{start_year[:2]}{end_year}"
            years.extend([start_year, end_year])

        years.extend(match.group(1) for match in _FOUR_DIGIT_YEAR_RE.finditer(question))

        unique_years: List[str] = []
        for year in years:
            normalized = year.strip()
            if len(normalized) == 2 and unique_years:
                normalized = f"{unique_years[0][:2]}{normalized}"
            if normalized and normalized not in unique_years:
                unique_years.append(normalized)
        return unique_years

    def _extract_metric_phrase(self, question: str) -> str:
        lower = question.lower()
        patterns = [
            r"delta in (.+?) from \d{4}",
            r"change in (.+?) from \d{4}",
            r"compare (.+?) between \d{4}",
            r"what was (.+?) in \d{4}",
            r"how much was (.+?) in \d{4}",
        ]
        for pattern in patterns:
            match = re.search(pattern, lower)
            if match:
                candidate = match.group(1).strip(" .?")
                if candidate:
                    return candidate
        return ""

    def _metric_aliases(self, text: str) -> List[str]:
        lower = text.lower()
        aliases: List[str] = []
        for terms in _FINANCE_METRIC_TERMS.values():
            if any(term in lower for term in terms):
                aliases.extend(terms)
        if not aliases and ("rev" in lower or "revenue" in lower):
            aliases.extend(_FINANCE_METRIC_TERMS["revenue"])
        deduped: List[str] = []
        for alias in aliases:
            if alias not in deduped:
                deduped.append(alias)
        return deduped

    def _metric_scope_tokens(
        self,
        text: str,
        *,
        metric_aliases: Sequence[str] = (),
        anchor_entity: str = "",
    ) -> List[str]:
        lower = text.lower().replace("&", " and ")
        tokens = re.findall(r"[a-z][a-z0-9]+", lower)
        anchor_tokens = {
            token
            for token in re.findall(r"[a-z][a-z0-9]+", normalize_entity(anchor_entity).lower())
            if token
        }
        alias_tokens = {
            token
            for alias in metric_aliases
            for token in re.findall(r"[a-z][a-z0-9]+", str(alias).lower())
            if token
        }
        result: List[str] = []
        for token in tokens:
            if (
                token in _METRIC_TOKEN_STOPWORDS
                or token in _GENERIC_METRIC_TOKENS
                or token in anchor_tokens
                or token in alias_tokens
            ):
                continue
            if token not in result:
                result.append(token)
        return result

    def _is_financial_delta_question(
        self,
        question: str,
        raw_intent_name: str,
        years: Sequence[str],
        metric_aliases: Sequence[str],
    ) -> bool:
        if raw_intent_name == "financial_metric_delta":
            return True
        return bool(_FINANCE_DELTA_RE.search(question) and len(years) >= 2 and metric_aliases)

    def _is_financial_metric_question(
        self,
        question: str,
        raw_intent_name: str,
        years: Sequence[str],
        metric_aliases: Sequence[str],
    ) -> bool:
        if raw_intent_name in {"financial_metric_lookup", "financial_metric_delta"}:
            return True
        lower = question.lower()
        return bool(metric_aliases and (years or any(term in lower for terms in _FINANCE_METRIC_TERMS.values() for term in terms)))

    def _is_legal_issue_question(self, question: str, raw_intent_name: str) -> bool:
        if raw_intent_name == "relationship_lookup":
            return False
        if "LegalIssue" not in self.ontology.nodes:
            return False
        if not any(
            rel_def.source == "Company" and rel_def.target == "LegalIssue"
            for rel_def in self.ontology.relationships.values()
        ):
            return False
        lower = question.lower()
        legal_markers = (
            "legal issue",
            "legal issues",
            "lawsuit",
            "lawsuits",
            "litigation",
            "investigation",
            "investigations",
            "claim",
            "claims",
            "proceeding",
            "proceedings",
        )
        return any(marker in lower for marker in legal_markers)
