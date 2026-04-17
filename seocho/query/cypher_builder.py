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
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
    "assets": ["asset", "assets"],
    "liabilities": ["liability", "liabilities"],
    "cash flow": ["cash flow", "cashflow"],
}
_METRIC_TOKEN_STOPWORDS = {
    "delta", "difference", "change", "from", "to", "between", "compare", "comparison",
    "what", "was", "is", "the", "of", "in", "for", "did", "does", "how", "much",
    "many", "by", "show", "tell", "me", "and", "or", "fiscal", "year", "years",
}
_GENERIC_METRIC_TOKENS = {
    "revenue", "revenues", "rev", "income", "profit", "expense", "expenses",
    "cost", "costs", "margin", "margins", "assets", "liabilities", "cash", "flow",
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
    ) -> Tuple[str, Dict[str, Any]]:
        if anchor_label and anchor_label not in self.ontology.nodes:
            anchor_label = ""

        if relationship_type and relationship_type not in self.ontology.relationships:
            relationship_type = self._match_relationship(
                relationship_type,
                anchor_label=anchor_label,
                target_label=target_label,
            )

        if intent == "entity_lookup":
            return self._entity_lookup(anchor_entity, anchor_label, workspace_id, limit)
        if intent == "relationship_lookup":
            return self._relationship_lookup(
                anchor_entity,
                anchor_label,
                target_entity,
                target_label,
                relationship_type,
                workspace_id,
                limit,
            )
        if intent in {"financial_metric_lookup", "financial_metric_delta"}:
            return self._financial_metric_lookup(
                anchor_entity=anchor_entity,
                metric_name=metric_name or target_entity,
                metric_aliases=metric_aliases or (),
                metric_scope_tokens=metric_scope_tokens or (),
                years=years or (),
                workspace_id=workspace_id,
                limit=limit,
            )
        if intent == "neighbors":
            return self._neighbors(anchor_entity, anchor_label, workspace_id, limit)
        if intent == "path":
            return self._path(anchor_entity, target_entity, workspace_id, limit)
        if intent == "count":
            return self._count(anchor_label, workspace_id)
        if intent == "list_all":
            return self._list_all(anchor_label, workspace_id, limit)
        return self._neighbors(anchor_entity, anchor_label, workspace_id, limit)

    def normalize_intent(self, question: str, raw_intent: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Coerce an LLM intent payload into a safer structured form."""
        intent_data = dict(raw_intent or {})
        raw_intent_name = str(intent_data.get("intent", "")).strip()
        years = self._extract_years(question, intent_data.get("years"))
        metric_name = str(intent_data.get("metric_name") or "").strip()
        if not metric_name:
            metric_name = self._extract_metric_phrase(question)
        metric_aliases = self._metric_aliases(metric_name or question)
        metric_scope_tokens = self._metric_scope_tokens(metric_name or question)

        if self._is_financial_delta_question(question, raw_intent_name, years, metric_aliases):
            intent = "financial_metric_delta"
        elif self._is_financial_metric_question(question, raw_intent_name, years, metric_aliases):
            intent = "financial_metric_lookup"
        else:
            intent = raw_intent_name or "neighbors"

        if intent in {"financial_metric_lookup", "financial_metric_delta"}:
            intent_data["anchor_label"] = str(intent_data.get("anchor_label") or "Company")
            intent_data["target_label"] = str(intent_data.get("target_label") or "FinancialMetric")

        intent_data["intent"] = intent
        intent_data["metric_name"] = metric_name
        intent_data["metric_aliases"] = metric_aliases
        intent_data["metric_scope_tokens"] = metric_scope_tokens
        intent_data["years"] = years
        return intent_data

    def intent_extraction_prompt(self) -> str:
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

        return (
            "You are a question analyzer for a knowledge graph.\n"
            "Given a user question, extract the INTENT — do NOT generate Cypher.\n\n"
            "IMPORTANT: The graph was built using the ontology below.\n"
            "You MUST use ONLY the node types and relationship types listed here.\n"
            "Do NOT invent new types — if the question implies a relationship not in the list,\n"
            "use the closest matching relationship or set relationship_type to empty.\n\n"
            f"Ontology query profile: package_id={profile['package_id']}, "
            f"version={profile['version']}, graph_model={profile['graph_model']}.\n"
            f"Deterministic intents supported: {', '.join(profile['deterministic_intents'])}.\n\n"
            f"Node types:\n{node_block}\n\n"
            f"Relationship types (ONLY these exist in the graph):\n{rel_block}\n\n"
            "Return a JSON object with:\n"
            '  "intent": one of "entity_lookup", "relationship_lookup", "neighbors", "path", "count", "list_all", "financial_metric_lookup", "financial_metric_delta"\n'
            '  "anchor_entity": the main entity name mentioned\n'
            f'  "anchor_label": one of [{", ".join(labels)}] or empty\n'
            '  "target_entity": secondary entity (if asking about a relationship)\n'
            '  "target_label": secondary entity type\n'
            f'  "relationship_type": one of [{", ".join(self.ontology.relationships.keys())}] or empty\n'
            '  "metric_name": financial metric or line-item phrase when asking about a metric value or delta\n'
            '  "years": list of years mentioned in the question\n\n'
            "Examples with this ontology:\n"
            '  "Who works at Samsung?" → {"intent": "relationship_lookup", "anchor_entity": "Samsung", "anchor_label": "Company", "relationship_type": "EMPLOYS"}\n'
            '  "Tell me about Apple" → {"intent": "neighbors", "anchor_entity": "Apple", "anchor_label": "Company"}\n'
            '  "How many companies?" → {"intent": "count", "anchor_label": "Company"}\n'
            '  "Delta in CBOE Data & Access Solutions rev from 2021-23." → {"intent": "financial_metric_delta", "anchor_entity": "CBOE", "anchor_label": "Company", "metric_name": "Data & Access Solutions revenue", "years": ["2021", "2023"]}\n'
        )

    def _entity_lookup(self, entity: str, label: str, workspace_id: str, limit: int) -> Tuple[str, Dict[str, Any]]:
        label_clause = f":{label}" if label else ""
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
        a_label = f":{anchor_label}" if anchor_label else ""
        t_label = f":{target_label}" if target_label else ""
        rel_clause = f":{self._rel_name(rel_type)}" if rel_type else ""

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
        label_clause = f":{label}" if label else ""
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
        label_clause = f":{label}" if label else ""
        return (
            f"MATCH (n{label_clause})\n"
            "WHERE $workspace_id = '' OR coalesce(n._workspace_id, '') = $workspace_id\n"
            "RETURN count(n) AS count",
            {"workspace_id": workspace_id},
        )

    def _list_all(self, label: str, workspace_id: str, limit: int) -> Tuple[str, Dict[str, Any]]:
        label_clause = f":{label}" if label else ""
        return (
            f"MATCH (n{label_clause})\n"
            "WHERE $workspace_id = '' OR coalesce(n._workspace_id, '') = $workspace_id\n"
            "RETURN coalesce(n.name, n.uri, elementId(n)) AS name, labels(n) AS labels\n"
            "ORDER BY name\n"
            "LIMIT $limit",
            {"workspace_id": workspace_id, "limit": limit},
        )

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
        relationship_candidates = self._relationship_candidates(
            source_label="Company",
            target_label="FinancialMetric",
        )
        return (
            "MATCH (c:Company)-[r]-(m:FinancialMetric)\n"
            "WHERE (toLower(coalesce(c.name, c.uri, '')) CONTAINS toLower($anchor)\n"
            "   OR toLower(coalesce(c.name, c.uri, '')) CONTAINS toLower($anchor_norm))\n"
            "  AND ($workspace_id = '' OR (coalesce(c._workspace_id, '') = $workspace_id AND coalesce(m._workspace_id, '') = $workspace_id))\n"
            "  AND ($relationship_candidates = [] OR type(r) IN $relationship_candidates)\n"
            "  AND ($metric_aliases = [] OR ANY(alias IN $metric_aliases WHERE toLower(coalesce(m.name, m.uri, '')) CONTAINS alias))\n"
            "  AND ($metric_scope_tokens = [] OR ALL(token IN $metric_scope_tokens WHERE toLower(coalesce(m.name, m.uri, '')) CONTAINS token))\n"
            "  AND ($years = [] OR ANY(year IN $years WHERE coalesce(toString(m.year), '') = year OR toLower(coalesce(m.name, m.uri, '')) CONTAINS year))\n"
            "RETURN coalesce(c.name, c.uri) AS company,\n"
            "       coalesce(m.name, m.uri) AS metric_name,\n"
            "       coalesce(toString(m.year), '') AS year,\n"
            "       CASE WHEN m.value IS NULL THEN '' ELSE toString(m.value) END AS value,\n"
            "       type(r) AS relationship,\n"
            "       coalesce(m.content_preview, c.content_preview, m.description, c.description, '') AS supporting_fact\n"
            "ORDER BY company, year, metric_name\n"
            "LIMIT $limit",
            {
                "anchor": anchor_entity,
                "anchor_norm": normalize_entity(anchor_entity),
                "metric_name": metric_name,
                "metric_aliases": [alias.lower() for alias in metric_aliases if alias],
                "metric_scope_tokens": [token.lower() for token in metric_scope_tokens if token],
                "years": [str(year) for year in years if str(year).strip()],
                "relationship_candidates": relationship_candidates,
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

    def _metric_scope_tokens(self, text: str) -> List[str]:
        lower = text.lower().replace("&", " and ")
        tokens = re.findall(r"[a-z][a-z0-9]+", lower)
        result: List[str] = []
        for token in tokens:
            if token in _METRIC_TOKEN_STOPWORDS or token in _GENERIC_METRIC_TOKENS:
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
