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


def active_graph_predicate(alias: str) -> str:
    """Exclude graph rows superseded by an approved remediation."""

    return f"coalesce({alias}._superseded_by, '') = ''"


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

        # The relationship may be valid yet oriented the wrong way round. The
        # ontology declares the direction, so a contradicting (anchor, target)
        # pair is a slot-fill error the guardrail can repair instead of emitting
        # a traversal that matches nothing.
        anchor_label, target_label = self._orient_relationship(
            relationship_type, anchor_label, target_label
        )

        # Which end of the edge the anchor sits on. Carried as instance state rather than
        # a template kwarg so every registered PatternSpec factory keeps its signature —
        # the same approach ``last_orientation_repair`` already uses, and safe because a
        # builder is constructed per plan.
        self.anchor_role = str(hint_payload.get("anchor_role", "") or "")

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
        role_lines = []
        card_lines = []
        for rtype, rd in self.ontology.relationships.items():
            desc = rd.description or rtype
            rel_descriptions.append(f"  - {rtype}: ({rd.source})→({rd.target}) — {desc}")
            src_role = getattr(rd, "source_role", "") or ""
            tgt_role = getattr(rd, "target_role", "") or ""
            hint = getattr(rd, "degree_hint", None) or {}
            if hint.get("heavy_tailed"):
                card_lines.append(
                    f"  - {rtype}: median out-degree {hint.get('median_out')}, "
                    f"p99 {hint.get('p99_out')}, maximum {hint.get('max_out'):,} — a few "
                    f"nodes carry orders of magnitude more edges than the typical one"
                )
            if src_role or tgt_role:
                role_lines.append(
                    f"  - {rtype}: the tail of the arrow is the "
                    f"{src_role or rd.source}, the head is the {tgt_role or rd.target}"
                )
        rel_block = "\n".join(rel_descriptions) if rel_descriptions else "  (none defined)"
        # Roles are asked for only when the ontology declares them, so an ontology that
        # never needed the distinction is not handed a field it cannot answer.
        role_block = (
            "Directional roles (both endpoints may share a label, so the arrow — not the "
            "label — carries the direction):\n" + "\n".join(role_lines) + "\n\n"
        ) if role_lines else ""
        # Stated so intent classification can route a question that would walk an
        # unbounded neighbourhood, rather than discovering it as a timeout.
        card_block = (
            "Degree distribution (measured on the loaded data):\n"
            + "\n".join(card_lines)
            + "\n  Multi-hop expansion from a high-degree node is unbounded work. Prefer "
              "an intent whose answer can stop early; a question requiring an exhaustive "
              "count or ranking over such a neighbourhood is expensive by nature.\n\n"
        ) if card_lines else ""

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
            f"{role_block}"
            f"{card_block}"
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
            '  "years": list of years mentioned in the question\n'
            + (
                '  "anchor_role": which end of the relationship the anchor entity sits on '
                '— "source" if the anchor is the tail of the arrow (it acts), "target" if '
                'the anchor is the head (it is acted upon), empty if the question is '
                'symmetric or the direction is genuinely unclear\n'
                if role_lines else ""
            )
            + "\n"
            "Verification:\n"
            "- Before finalizing, check that the json is valid.\n"
            "- Check that labels and relationship types are from the allowed ontology lists.\n"
            "- Check that empty fields stay empty instead of being guessed.\n\n"
            "Examples with this ontology:\n"
            '  "Who works at Samsung?" → {"intent": "relationship_lookup", "anchor_entity": "Samsung", "anchor_label": "Company", "relationship_type": "EMPLOYS"}\n'
            '  "Tell me about Apple" → {"intent": "neighbors", "anchor_entity": "Apple", "anchor_label": "Company"}\n'
            '  "How many companies?" → {"intent": "count", "anchor_label": "Company"}\n'
            + (
                '  "How many accounts did account 42 pay?" → anchor_role "source" '
                '(account 42 acts; the answer is its counterparties on the head side)\n'
                '  "Which accounts funded account 42?" → anchor_role "target" '
                '(account 42 is acted upon)\n'
                if role_lines else ""
            ) +
            '  "How many accounts sent transfers into account 42?" → {"intent": "count", "anchor_entity": "42", '
            '"anchor_label": "Account", "target_label": "Account", "relationship_type": "TRANSFER"}\n'
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
            "anchor_role": self._anchor_role(
                question, relationship_type,
                extracted=str(intent_data.get("anchor_role", "") or "").strip().lower()),
            "optimization_hints": {
                "anchor_access": "label_plus_indexed_key",
                "require_workspace_filter_pushdown": True,
                "max_graph_hops": 4,
                "max_result_rows": 50,
                "relationship_triplets": [
                    {
                        "source_label": rel_def.source,
                        "relationship_type": rel_name,
                        "target_label": rel_def.target,
                    }
                    for rel_name, rel_def in self.ontology.relationships.items()
                    if not relationship_candidates or rel_name in relationship_candidates[:6]
                ],
                "avoid": [
                    "AllNodesScan",
                    "unbounded_variable_length_expand",
                    "late_workspace_filter",
                    "cartesian_product",
                ],
            },
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
        optimization = hints.get("optimization_hints", {})
        if isinstance(optimization, dict) and optimization:
            lines.append(
                "- Query optimization contract: start from a labelled node using an indexed key; "
                "push the workspace predicate into the first match."
            )
            lines.append(
                f"- Hard budgets: max_graph_hops={int(optimization.get('max_graph_hops', 4))}, "
                f"max_result_rows={int(optimization.get('max_result_rows', 50))}."
            )
            triplets = optimization.get("relationship_triplets", [])
            if isinstance(triplets, list) and triplets:
                rendered = [
                    f"({item.get('source_label', '')})-[:{item.get('relationship_type', '')}]->({item.get('target_label', '')})"
                    for item in triplets
                    if isinstance(item, dict)
                ]
                if rendered:
                    lines.append("- Allowed typed expansions: " + ", ".join(rendered))
            avoid = optimization.get("avoid", [])
            if isinstance(avoid, list) and avoid:
                lines.append("- Avoid plan shapes: " + ", ".join(str(item) for item in avoid))
        return "\n".join(lines)

    def _text_anchor(self, alias: str, entity: str, *,
                     value_param: str = "entity", norm_param: str = "entity_norm",
                     include_id: bool = False) -> Tuple[str, Dict[str, Any]]:
        """Fuzzy fallback anchor: CONTAINS over the conventional display properties.

        Kept for genuinely free-text mentions. It cannot use an index, so callers
        should try :meth:`_sargable_anchor` first.
        """
        chain = f"coalesce({alias}.name, {alias}.uri, {alias}.id, '')" if include_id \
            else f"coalesce({alias}.name, {alias}.uri, '')"
        predicate = (f"(toLower({chain}) CONTAINS toLower(${value_param})\n"
                     f"   OR toLower({chain}) CONTAINS toLower(${norm_param}))")
        return predicate, {value_param: entity, norm_param: normalize_entity(entity)}

    def _sargable_anchor_in(self, alias: str, label: str, tokens: Sequence[str],
                            *, param: str = "anchor_keys") -> Optional[Tuple[str, Dict[str, Any]]]:
        """Index-usable membership test for a list of identifier mentions.

        "channels used by accounts 9000001, 9000002 and 9000003" names several
        anchors. ``key IN $list`` is index-eligible, whereas CONTAINS over a token
        list is not — that shape cost 86M dbHits at SF1000 while returning the
        right answer, which is exactly the failure mode a correctness-only test
        cannot see.
        """
        cleaned = [tok.strip() for tok in tokens if tok and tok.strip()]
        if not cleaned or not label:
            return None
        ontology = getattr(self, "ontology", None)
        node = ontology.nodes.get(label) if ontology is not None else None
        if node is None:
            return None
        keys = list(getattr(node, "effective_identity_keys", []) or [])
        for name in getattr(node, "indexed_properties", []) or []:
            if name not in keys:
                keys.append(name)
        if not keys:
            return None

        clauses: List[str] = []
        params: Dict[str, Any] = {}
        for index, key in enumerate(keys):
            prop = node.properties.get(key)
            declared = str(getattr(getattr(prop, "type", None), "value", "") or "").upper()
            values: List[Any] = []
            for tok in cleaned:
                if declared in {"INTEGER", "INT", "LONG"}:
                    try:
                        values.append(int(tok))
                    except ValueError:
                        continue
                elif declared in {"FLOAT", "DOUBLE"}:
                    try:
                        values.append(float(tok))
                    except ValueError:
                        continue
                else:
                    values.append(tok)
            if not values:
                continue
            name = f"{alias}_{param}_{index}"
            clauses.append(f"{alias}.{quote_identifier(key)} IN ${name}")
            params[name] = values
        if not clauses:
            return None
        return "(" + " OR ".join(clauses) + ")", params

    def _anchor_predicate(self, alias: str, label: str, entity: str, *,
                          value_param: str = "entity", norm_param: str = "entity_norm",
                          include_id: bool = False) -> Tuple[str, Dict[str, Any]]:
        """Index-usable equality when the mention identifies a node, else fuzzy text.

        Every anchored template routes through here so a single decision — "is this
        an identifier or prose?" — governs plan shape everywhere. Applying it to only
        one template is what left `_list_all` doing 86M dbHits at SF1000 while the
        repaired `_count` did 105.
        """
        sargable = self._sargable_anchor(alias, label, entity)
        if sargable is not None:
            return sargable
        return self._text_anchor(alias, entity, value_param=value_param,
                                 norm_param=norm_param, include_id=include_id)

    def _entity_lookup(self, entity: str, label: str, workspace_id: str, limit: int) -> Tuple[str, Dict[str, Any]]:
        label_clause = f":{quote_identifier(label)}" if label else ""
        predicate, params = self._anchor_predicate("n", label, entity)
        params.update({"workspace_id": workspace_id, "limit": limit})
        return (
            f"MATCH (n{label_clause})\n"
            f"WHERE {predicate}\n"
            "  AND ($workspace_id = '' OR coalesce(n._workspace_id, '') = $workspace_id)\n"
            f"  AND {active_graph_predicate('n')}\n"
            "RETURN n\n"
            "LIMIT $limit",
            params,
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

        anchor_pred, anchor_params = self._anchor_predicate(
            "a", anchor_label, anchor, value_param="anchor", norm_param="anchor_norm")
        where_parts = [
            anchor_pred,
            "($workspace_id = '' OR (coalesce(a._workspace_id, '') = $workspace_id AND coalesce(b._workspace_id, '') = $workspace_id))",
            active_graph_predicate("a"), active_graph_predicate("b"),
        ]
        params: Dict[str, Any] = {**anchor_params, "workspace_id": workspace_id, "limit": limit}

        if target:
            target_pred, target_params = self._anchor_predicate(
                "b", target_label, target, value_param="target", norm_param="target_norm")
            where_parts.append(target_pred)
            params.update(target_params)

        where = " AND ".join(where_parts)
        # Two defects lived here, both invisible until the endpoints share a label
        # (Account -TRANSFER-> Account), which is every payments/ownership schema.
        #
        # 1. The projection named the ANCHOR `source` and the neighbour `target`
        #    regardless of which way the arrow pointed. Matching undirected then
        #    labelling by binding order inverts every incoming edge: asked "which
        #    accounts transferred to B2", it returned B2 as the source of A1's
        #    transfer and the model answered the exact opposite of the graph, with
        #    no error raised. Read the direction off the edge instead (seocho-k5n).
        # 2. The pattern stayed undirected even when the ontology declared which end
        #    the anchor sits on, so recall ignored the question's direction. Honour
        #    `anchor_role` — the same flip `_count` (seocho-k2v) and `_list_all`
        #    (seocho-pl1) already apply.
        #
        # `target_labels` / `target_properties` / `supporting_fact` stay bound to
        # `b`, the anchor's counterpart, which is what an answer needs; only the
        # source/target naming was ever a claim about direction.
        role = getattr(self, "anchor_role", "")
        if role == "source":
            pattern = f"MATCH (a{a_label})-[r{rel_clause}]->(b{t_label})"
        elif role == "target":
            pattern = f"MATCH (a{a_label})<-[r{rel_clause}]-(b{t_label})"
        else:
            pattern = f"MATCH (a{a_label})-[r{rel_clause}]-(b{t_label})"
        return (
            f"{pattern}\n"
            f"WHERE {where}\n"
            "RETURN coalesce(startNode(r).name, startNode(r).uri) AS source,\n"
            "       type(r) AS relationship,\n"
            "       coalesce(endNode(r).name, endNode(r).uri) AS target,\n"
            "       labels(b) AS target_labels,\n"
            "       properties(b) AS target_properties,\n"
            "       coalesce(b.content_preview, b.description, b.content, '') AS supporting_fact\n"
            "LIMIT $limit",
            params,
        )

    def _neighbors(self, entity: str, label: str, workspace_id: str, limit: int,
                   *, target_label: str = "", relationship_type: str = "") -> Tuple[str, Dict[str, Any]]:
        label_clause = f":{quote_identifier(label)}" if label else ""
        anchor_predicate, anchor_params = self._anchor_predicate("n", label, entity)

        # "Which accounts did X transfer to" arrives here with the relationship and
        # target already resolved (anchor_label=Account, target_label=Account,
        # relationship_type=TRANSFER), and the generic neighbour summary below throws
        # all three away: it expands undirected over every relationship and collapses
        # the result into one row. Honour the slots when they are present — the same
        # defect previously fixed in _count (seocho-k2v) and _list_all (seocho-pl1).
        if relationship_type in self.ontology.relationships:
            tgt_clause = f":{quote_identifier(target_label)}" if target_label in self.ontology.nodes else ""
            arrow = ">" if self._path_direction([relationship_type]) else ""
            return (
                f"MATCH (n{label_clause})-[:{quote_identifier(relationship_type)}]-{arrow}(m{tgt_clause})\n"
                f"WHERE {anchor_predicate}\n"
                "  AND ($workspace_id = '' OR coalesce(n._workspace_id, '') = $workspace_id)\n"
                f"  AND {active_graph_predicate('n')}\n"
                f"  AND {active_graph_predicate('m')}\n"
                f"RETURN DISTINCT {self._display_expr('m', target_label)} AS neighbor,\n"
                "       labels(m) AS neighbor_labels\n"
                "ORDER BY neighbor\n"
                "LIMIT $limit",
                {**anchor_params, "workspace_id": workspace_id, "limit": limit},
            )

        return (
            f"MATCH (n{label_clause})\n"
            f"WHERE {anchor_predicate}\n"
            "  AND ($workspace_id = '' OR coalesce(n._workspace_id, '') = $workspace_id)\n"
            f"  AND {active_graph_predicate('n')}\n"
            "OPTIONAL MATCH (n)-[r]-(m)\n"
            "WHERE ($workspace_id = '' OR coalesce(m._workspace_id, '') = $workspace_id)\n"
            f"  AND {active_graph_predicate('m')}\n"
            "RETURN coalesce(n.name, n.uri) AS entity,\n"
            "       properties(n) AS properties,\n"
            "       collect(DISTINCT {\n"
            "         relation: type(r),\n"
            "         neighbor: coalesce(m.name, m.uri),\n"
            "         neighbor_labels: labels(m)\n"
            "       })[0..$limit] AS neighbors,\n"
            "       coalesce(n.content_preview, n.description, n.content, '') AS supporting_fact\n"
            "LIMIT 1",
            {**anchor_params, "workspace_id": workspace_id, "limit": limit},
        )

    def _reachable_labels(
        self, start_label: str, *, relationship_types: Optional[Sequence[str]] = None,
        max_hops: int = 4, undirected: bool = False,
    ) -> Set[str]:
        """Labels reachable from ``start_label`` within ``max_hops``, per the ontology.

        A variable-length pattern is only satisfiable if the ontology admits a chain
        of declared relationships between the endpoint labels. ``TRANSFER`` is
        declared ``Account -> Account``, so repeating it never leaves ``Account`` —
        an ``Account -> Company`` path over ``TRANSFER*`` is provably empty and the
        engine should not be asked to search for it. This is the LPG analogue of the
        type-inference pruning formalised for recursive graph queries in
        "Schema-Based Query Optimisation for Graph Databases" (SIGMOD 2025).
        """
        ontology = getattr(self, "ontology", None)
        if ontology is None or not start_label:
            return set()
        allowed = (
            {r for r in relationship_types if r in ontology.relationships}
            if relationship_types else set(ontology.relationships)
        )
        # ``undirected`` decides what "reachable" means, and the two uses differ:
        # pruning must be conservative (only refuse a pattern nothing could satisfy,
        # so it walks edges both ways), while choosing to emit a directed arrow needs
        # the strict directed answer.
        edges: List[Tuple[str, str]] = []
        for rtype in allowed:
            rel = ontology.relationships[rtype]
            edges.append((rel.source, rel.target))
            if undirected:
                edges.append((rel.target, rel.source))

        frontier = {start_label}
        seen = {start_label}
        for _ in range(max(1, int(max_hops))):
            nxt: Set[str] = set()
            for source, target in edges:
                if source in {"Any", ""} or source in frontier:
                    for label in ({target} if target not in {"Any", ""} else set(ontology.nodes)):
                        if label not in seen:
                            nxt.add(label)
            if not nxt:
                break
            seen |= nxt
            frontier = nxt
        return seen

    def _path_direction(self, relationship_types: Sequence[str]) -> str:
        """``->`` when every candidate relationship agrees on direction, else undirected.

        An undirected variable-length pattern explores both ways, which on a large
        graph is the difference between a bounded traversal and a scan. When the
        ontology fixes the direction there is no reason to pay for the ambiguity.
        """
        ontology = getattr(self, "ontology", None)
        if ontology is None or not relationship_types:
            return ""
        pairs = {
            (ontology.relationships[r].source, ontology.relationships[r].target)
            for r in relationship_types if r in ontology.relationships
        }
        if not pairs:
            return ""
        # Self-referential (Account->Account) or a single consistent orientation.
        return ">" if len(pairs) == 1 else ""

    def _path(
        self,
        from_entity: str,
        to_entity: str,
        workspace_id: str,
        limit: int,
        *,
        anchor_label: str = "",
        target_label: str = "",
        relationship_type: str = "",
        max_hops: int = 4,
    ) -> Tuple[str, Dict[str, Any]]:
        self.last_path_pruning: Optional[Dict[str, Any]] = None
        bounded_hops = max(1, min(int(max_hops), 4))
        requested = [relationship_type] if relationship_type else []

        # Schema-reachability pruning. Asking the engine to search for a path the
        # ontology rules out is what let one generated query run 9m13s at SF1000
        # (seocho-z1q); the hop bound constrains the shape but not satisfiability.
        rel_types = list(requested)
        if anchor_label and target_label:
            # Conservative test: walk declared edges in both directions, so a pattern
            # is only refused when no chain could satisfy it either way. Pruning on
            # directed reachability would wrongly empty legitimate undirected
            # questions ("how is account X connected to company Y", where OWN is
            # declared Company -> Account).
            if target_label not in self._reachable_labels(
                anchor_label, relationship_types=requested or None,
                max_hops=bounded_hops, undirected=True,
            ):
                widened = self._reachable_labels(
                    anchor_label, max_hops=bounded_hops, undirected=True)
                if target_label in widened:
                    # Reachable, just not over the relationship the model named:
                    # drop the restriction rather than return a wrong empty answer.
                    self.last_path_pruning = {
                        "action": "relaxed_relationship_type",
                        "requested": relationship_type,
                        "reason": "target_label_unreachable_over_requested_relationship",
                    }
                    rel_types = []
                else:
                    # Provably empty per the schema: answer immediately instead of
                    # exhaustively searching for something that cannot exist.
                    self.last_path_pruning = {
                        "action": "pruned_unsatisfiable",
                        "requested": relationship_type,
                        "anchor_label": anchor_label,
                        "target_label": target_label,
                        "reason": "no_declared_relationship_chain_between_endpoint_labels",
                    }
                    return (
                        "RETURN [] AS nodes, [] AS relationships\n"
                        "LIMIT 0",
                        {"workspace_id": workspace_id, "limit": limit},
                    )

        a_label = f":{quote_identifier(anchor_label)}" if anchor_label else ""
        b_label = f":{quote_identifier(target_label)}" if target_label else ""
        rel_name = self._rel_name(rel_types[0]) if rel_types else ""
        rel_clause = f":{quote_identifier(rel_name)}" if rel_name else ""
        # Only orient the pattern when the target is genuinely reachable following
        # declared directions; otherwise the question needs undirected traversal and
        # forcing an arrow would return nothing.
        arrow = ""
        if anchor_label and target_label and target_label in self._reachable_labels(
            anchor_label, relationship_types=rel_types or None, max_hops=bounded_hops
        ):
            arrow = self._path_direction(rel_types)

        from_pred, from_params = self._anchor_predicate(
            "a", anchor_label, from_entity, value_param="from_e", norm_param="from_e_norm")
        to_pred, to_params = self._anchor_predicate(
            "b", target_label, to_entity, value_param="to_e", norm_param="to_e_norm")
        params: Dict[str, Any] = {**from_params, **to_params,
                                  "workspace_id": workspace_id, "limit": limit}
        return (
            f"MATCH path = shortestPath((a{a_label})-[{rel_clause}*..{bounded_hops}]-{arrow}(b{b_label}))\n"
            f"WHERE {from_pred}\n"
            f"  AND {to_pred}\n"
            "  AND ($workspace_id = '' OR all(n IN nodes(path) WHERE coalesce(n._workspace_id, '') = $workspace_id))\n"
            f"RETURN [n IN nodes(path) | {self._display_expr('n', anchor_label)}] AS nodes,\n"
            "       [r IN relationships(path) | type(r)] AS relationships\n"
            "LIMIT $limit",
            params,
        )

    def _count(
        self,
        label: str,
        workspace_id: str,
        *,
        anchor_entity: str = "",
        target_label: str = "",
        relationship_type: str = "",
    ) -> Tuple[str, Dict[str, Any]]:
        # Relationship-aware count: "how many <label> <relationship> <anchor>"
        # aggregates the distinct counterparties of the anchor over that
        # relationship. Without this, such questions collapse into a full-label
        # scan and report the total node count.
        if anchor_entity and relationship_type in self.ontology.relationships:
            src_label = target_label if target_label in self.ontology.nodes else label
            src_clause = f":{quote_identifier(src_label)}" if src_label else ""
            rel_clause = f":{quote_identifier(relationship_type)}"
            # Anchor label matters twice: it makes the pattern index-eligible and it
            # tells us which identity key to compare against.
            anchor_label = label if label in self.ontology.nodes else src_label
            anchor_clause = f":{quote_identifier(anchor_label)}" if anchor_label else ""
            sargable = self._sargable_anchor("anchor", anchor_label, anchor_entity)
            if sargable is not None:
                predicate, params = sargable
            else:
                predicate = (
                    "(toLower(coalesce(anchor.name, anchor.uri, anchor.id, '')) CONTAINS toLower($entity)\n"
                    "   OR toLower(coalesce(anchor.name, anchor.uri, anchor.id, '')) CONTAINS toLower($entity_norm))"
                )
                params = {"entity": anchor_entity, "entity_norm": normalize_entity(anchor_entity)}
            params["workspace_id"] = workspace_id
            # The anchor was previously always the arrow's target, i.e. every count was an
            # in-degree. That is right for "how many metrics does company X have"
            # (Company -> Metric) and wrong for "how many accounts did X transfer to",
            # which the schema cannot distinguish when both ends share a label. When the
            # ontology declares the anchor as the source end, emit the edge the other way.
            if getattr(self, "anchor_role", "") == "source":
                pattern = f"MATCH (anchor{anchor_clause})-[{rel_clause}]->(src{src_clause})"
            else:
                pattern = f"MATCH (src{src_clause})-[{rel_clause}]->(anchor{anchor_clause})"
            return (
                f"{pattern}\n"
                f"WHERE {predicate}\n"
                "  AND ($workspace_id = '' OR coalesce(src._workspace_id, '') = $workspace_id)\n"
                f"  AND {active_graph_predicate('src')}\n"
                f"  AND {active_graph_predicate('anchor')}\n"
                "RETURN count(DISTINCT src) AS count",
                params,
            )

        label_clause = f":{quote_identifier(label)}" if label else ""
        return (
            f"MATCH (n{label_clause})\n"
            "WHERE $workspace_id = '' OR coalesce(n._workspace_id, '') = $workspace_id\n"
            "RETURN count(n) AS count",
            {"workspace_id": workspace_id},
        )

    def _display_expr(self, alias: str, label: str = "") -> str:
        """Ontology-aware display expression for a node.

        ``name``/``uri`` are the conventional display properties, but a schema may
        key its nodes on something else (``Channel.code``, ``Account.id``).
        Falling straight through to ``elementId`` in that case leaks raw internal
        identifiers into answers, so consult the ontology for the label's UNIQUE
        (identity) properties before giving up.
        """
        candidates = ["name", "uri"]
        node = self.ontology.nodes.get(label) if label else None
        if node is not None:
            for prop_name, prop in getattr(node, "properties", {}).items():
                if getattr(prop, "unique", False) and prop_name not in candidates:
                    candidates.append(prop_name)
            for key in getattr(node, "identity_keys", []) or []:
                if key not in candidates:
                    candidates.append(key)
        chain = ", ".join(f"{alias}.{quote_identifier(c)}" for c in candidates)
        return f"coalesce({chain}, elementId({alias}))"

    def _list_all(
        self,
        label: str,
        workspace_id: str,
        limit: int,
        *,
        anchor_entity: str = "",
        target_label: str = "",
        relationship_type: str = "",
    ) -> Tuple[str, Dict[str, Any]]:
        # "List the <target> reached from <anchor> over <relationship>" is a
        # traversal, not a label dump. Without this the relationship/target slots
        # are discarded and every node of the anchor label is listed instead.
        if relationship_type in self.ontology.relationships and target_label in self.ontology.nodes:
            src_clause = f":{quote_identifier(label)}" if label else ""
            anchor_filter = ""
            params: Dict[str, Any] = {"workspace_id": workspace_id, "limit": limit}
            if anchor_entity:
                tokens = [tok.strip() for tok in re.split(r"[,\s]+", anchor_entity) if tok.strip()]
                # Prefer `key IN $list` — index-eligible — over CONTAINS per token.
                membership = self._sargable_anchor_in("src", label, tokens)
                if membership is not None:
                    predicate, extra = membership
                    anchor_filter = f"  AND {predicate}\n"
                    params.update(extra)
                else:
                    anchor_filter = (
                        "  AND any(tok IN $anchor_tokens WHERE "
                        f"toLower({self._display_expr('src', label)}) CONTAINS toLower(tok))\n"
                    )
                    params["anchor_tokens"] = tokens
            # Mirror of the count template. Here the anchor is already the arrow's source
            # and the listed rows are its targets — the outgoing reading — so only the
            # opposite phrasing ("which accounts transferred *to* X") needs the flip.
            rel_ref = quote_identifier(relationship_type)
            if getattr(self, "anchor_role", "") == "target":
                pattern = (f"MATCH (src{src_clause})<-[:{rel_ref}]-"
                           f"(tgt:{quote_identifier(target_label)})")
            else:
                pattern = (f"MATCH (src{src_clause})-[:{rel_ref}]->"
                           f"(tgt:{quote_identifier(target_label)})")
            return (
                f"{pattern}\n"
                "WHERE ($workspace_id = '' OR coalesce(src._workspace_id, '') = $workspace_id)\n"
                f"{anchor_filter}"
                f"  AND {active_graph_predicate('src')}\n"
                f"RETURN DISTINCT {self._display_expr('tgt', target_label)} AS name,\n"
                "       labels(tgt) AS labels\n"
                "ORDER BY name\n"
                "LIMIT $limit",
                params,
            )

        label_clause = f":{quote_identifier(label)}" if label else ""
        return (
            f"MATCH (n{label_clause})\n"
            "WHERE $workspace_id = '' OR coalesce(n._workspace_id, '') = $workspace_id\n"
            f"RETURN {self._display_expr('n', label)} AS name, labels(n) AS labels\n"
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

    def identity_candidates(self, question: str, label: str) -> List[Tuple[str, Any]]:
        """Tokens in the question that fit a declared identity key's type.

        Deterministic and free: the ontology already says which properties identify a node
        and what type they are, so a question naming "account 14079" yields 14079 without an
        extra model call.

        This exists because validated generation was given only ``workspace_id`` and
        ``limit`` as parameters. With no parameter for the anchor the model had to inline it
        as a literal — contradicting its own instruction never to insert literal IDs — and
        nothing downstream checked that an anchor was bound at all. One generated query
        dropped it entirely, matching every Account and expanding two hops from each:
        38,867,373 db hits for a question about a single account.
        """
        node = self.ontology.nodes.get(label) if getattr(self, "ontology", None) else None
        if node is None:
            return []
        # Which key the value belongs to has to travel with it. Passing the bare number left
        # the model to guess the property, and it chose `id` — a string like "Account:44957" —
        # so an integer compared against it matched nothing: one index seek, zero rows, and an
        # answer of 0 that looks like a real finding.
        numeric_key = next(
            (k for k in (getattr(node, "effective_identity_keys", []) or [])
             if str(getattr(getattr(node.properties.get(k), "type", None), "value", "")).upper()
             in {"INTEGER", "FLOAT"}), None)
        if numeric_key is None:
            return []
        # A digit run alone is not an identifier. "below 10000000" is a reporting threshold
        # and "within 7 days" is a window; binding either as the anchor is worse than binding
        # nothing, because a wrong anchor answers a different question confidently. So the
        # number has to be introduced by the node's own vocabulary — its label or an alias —
        # which is the same declared vocabulary `_strip_label_words` uses.
        vocabulary = [label] + list(getattr(node, "aliases", []) or [])
        words = "|".join(re.escape(w) for w in vocabulary if w)
        if not words:
            return []
        # Allows "account 14079", "Account #14079", "account number 14079" — the wording a
        # question actually uses — while "below 10000000" has no such introducer.
        pattern = rf"(?:{words})\s*(?:number|no\.?|#|id)?\s*[:#]?\s*(\d+)"
        return [(numeric_key, int(v))
                for v in re.findall(pattern, question or "", flags=re.IGNORECASE)]

    @staticmethod
    def _strip_label_words(entity: str, label: str, node: Any) -> str:
        """Drop a node-type word that the question wrapped around an identifier.

        Only the label and its declared aliases are removed, and only from the ends, so this
        cannot turn prose into a false identifier: "Samsung Electronics" contains no label
        word and survives unchanged, keeping its correct text-matching path.

        Both spellings matter — a question may say "Account 42" or "account 42" — so the
        comparison is case-insensitive while the returned value keeps its original case, which
        a string identity key may depend on.
        """
        words = [label] + list(getattr(node, "aliases", []) or [])
        vocabulary = {w.lower() for w in words if w}
        tokens = entity.split()
        while tokens and tokens[0].lower().strip(":#") in vocabulary:
            tokens.pop(0)
        while tokens and tokens[-1].lower().strip(":#") in vocabulary:
            tokens.pop()
        # Nothing left means the mention was only a type word and carries no identifier.
        # Returning it unchanged would produce `id = 'Account'`, an equality that matches
        # nothing while looking like a resolved anchor; returning empty lets the caller
        # decline and keep its text path.
        return " ".join(tokens)

    def _sargable_anchor(
        self, alias: str, label: str, entity: str
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Build an index-usable equality predicate for an identifying anchor.

        The conventional anchor predicate is ``CONTAINS`` over
        ``coalesce(name, uri, ...)``. It reads naturally and no index can serve it,
        so the plan degrades to a label scan. That is invisible on a small graph and
        fatal on a large one: measured on this schema, the scan costs 6.6M dbHits at
        SF1000 where an index seek costs 25, and it also stopped resolving the
        anchor correctly (answering 0 instead of 25).

        When the ontology declares what identifies a node — ``identity_keys``, else
        its UNIQUE property — and the mention looks like such an identifier, emit
        equality on that property instead. Values are coerced to the declared
        property type so an integer key is not compared against a string.

        Returns ``None`` when no identity key applies, leaving the caller on its
        existing text-matching path (correct for genuinely free-text mentions).
        """
        entity = (entity or "").strip()
        if not entity or not label:
            return None
        # Templates are also exercised on uninitialised builders (schema-shape
        # assertions that never touch an ontology), so absence of one degrades to
        # the text path rather than raising.
        ontology = getattr(self, "ontology", None)
        node = ontology.nodes.get(label) if ontology is not None else None
        if node is None:
            return None

        keys = list(getattr(node, "effective_identity_keys", []) or [])
        for name in getattr(node, "indexed_properties", []) or []:
            if name not in keys:
                keys.append(name)
        if not keys:
            return None

        # A mention may arrive with its own type word attached — "Account 42" rather than
        # "42" — because that is how the question named it. Stripping a leading or trailing
        # occurrence of the node's declared label or alias recovers the identifier, and uses
        # only vocabulary the ontology already holds.
        #
        # This is not cosmetic. Measured on the realistic question set: an extracted anchor of
        # 'Account 42' declined here and fell back to a text scan, while '42' from an
        # otherwise identical question produced an index seek. The only difference in the
        # question was that one sentence opened with "Account 42 is under review" and the
        # other said "freezing account 42" mid-sentence. At SF1 the scan still answers, so the
        # degradation is invisible; at SF1000 it costs 4,000,138 db hits against 130.
        entity = self._strip_label_words(entity, label, node)

        # An identifier mention is a single token. A multi-word mention that survived the
        # strip is prose ("Samsung Electronics"), and forcing it into equality against an id
        # would silently match nothing — worse than the slow-but-correct text match.
        if not entity or any(ch.isspace() for ch in entity):
            return None

        clauses: List[str] = []
        params: Dict[str, Any] = {}
        for index, key in enumerate(keys):
            prop = node.properties.get(key)
            declared = str(getattr(getattr(prop, "type", None), "value", "") or "").upper()
            # Namespace by alias: a path template anchors two nodes, and a shared
            # prefix silently compared the second endpoint against the first one's
            # value (b.id = $anchor_key_0), which returns nothing.
            param = f"{alias}_key_{index}"
            if declared in {"INTEGER", "FLOAT"}:
                # A numeric identity key can only match a numeric mention.
                try:
                    params[param] = int(entity) if declared == "INTEGER" else float(entity)
                except ValueError:
                    continue
            else:
                params[param] = entity
            clauses.append(f"{alias}.{quote_identifier(key)} = ${param}")

        if not clauses:
            return None
        return "(" + " OR ".join(clauses) + ")", params

    def _anchor_role(self, question: str, relationship_type: str,
                     extracted: str = "") -> str:
        """Which end of the edge the anchor sits on, per the ontology's declared phrasing.

        ``_orient_relationship`` repairs endpoints when the *labels* contradict the
        declared direction, and returns early when ``source == target`` because there is
        nothing a label comparison can say. That early return is exactly the case where
        direction matters most: ``TRANSFER: Account -> Account`` makes "which accounts did
        X send to" and "which accounts sent to X" identical to the schema, and query
        construction had to pick one. It picked anchor-as-target unconditionally, so every
        outgoing question was answered with an in-degree — correct-looking, always wrong,
        and invisible to label validation.

        Roles break the tie without guessing. The ontology declares which phrasings place
        the anchor at the source or the target end, and the anchor's role is read off the
        question against that declaration. Returning ``""`` when the ontology is silent or
        the evidence is balanced preserves the historical shape, so relationships that
        never needed this are unaffected.
        """
        rel = self.ontology.relationships.get(relationship_type) if relationship_type else None
        if rel is None:
            return ""
        # An explicitly extracted role wins. Substring matching against a hand-authored
        # phrase list does not generalise — measured 0 of 6 on paraphrases ("who did X
        # pay", "which accounts funded X") that mean the same thing in different words —
        # and a miss falls back to the historical anchor-as-target assumption, i.e. it
        # silently reinstates the bug. The ontology's job is to *name* the roles; mapping
        # arbitrary phrasing onto a named role is what the model is for. Phrases stay as a
        # deterministic fast path for the wordings a schema author cares to pin down.
        if extracted in {"source", "target"}:
            return extracted
        lowered = f" {(question or '').lower()} "
        # Longest match wins: a schema may declare both "transfer" and "transferred to",
        # and the more specific phrase is the one carrying the direction.
        source_hit = max(
            (len(p) for p in rel.source_phrases if p and p.lower() in lowered), default=0)
        target_hit = max(
            (len(p) for p in rel.target_phrases if p and p.lower() in lowered), default=0)
        if source_hit > target_hit:
            return "source"
        if target_hit > source_hit:
            return "target"
        return ""

    def _orient_relationship(
        self, relationship_type: str, anchor_label: str, target_label: str
    ) -> Tuple[str, str]:
        """Force the traversal to follow the direction the ontology declares.

        A model can name the right relationship and still reverse its endpoints
        (``anchor_label=Channel`` for ``USES_CHANNEL``, which the ontology defines
        as ``Account -> Channel``). The resulting Cypher is syntactically valid,
        passes label validation, and matches zero rows — a silent wrong answer.

        Since the ontology is authoritative about direction, the only orientation
        consistent with it is the declared one, so contradicting endpoints are
        repaired rather than executed. Repairs are recorded on
        ``self.last_orientation_repair`` so callers can measure how often the
        guardrail rescues a given model.
        """
        self.last_orientation_repair: Optional[Dict[str, Any]] = getattr(
            self, "last_orientation_repair", None
        )
        rel_def = self.ontology.relationships.get(relationship_type)
        if rel_def is None:
            return anchor_label, target_label

        source, target = rel_def.source, rel_def.target
        # "Any" endpoints impose no direction, and a self-referential
        # relationship cannot be reversed.
        if source in {"", "Any"} or target in {"", "Any"} or source == target:
            self.last_orientation_repair = None
            return anchor_label, target_label

        contradicts = (
            (anchor_label == target and target_label in {"", source})
            or (target_label == source and anchor_label in {"", target})
        )
        if not contradicts:
            self.last_orientation_repair = None
            return anchor_label, target_label

        self.last_orientation_repair = {
            "relationship_type": relationship_type,
            "from": {"anchor_label": anchor_label, "target_label": target_label},
            "to": {"anchor_label": source, "target_label": target},
            "reason": "reversed_endpoints_vs_ontology",
        }
        return source, target

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
        # The override this gates hardcodes anchor_label="Company" and
        # target_label="FinancialMetric", so it is only ever valid on an ontology that
        # declares them. Without this guard it fires on any schema and then emits a pattern
        # over labels that do not exist, which degrades to an unlabeled scan.
        #
        # Measured: rewriting the AML questions into the language an analyst actually uses
        # ("Account X is under review for acting as a payment mule… how many counterparty
        # accounts has it sent funds to?") tripped this on 15 of 24 runs. Words like *funds*
        # and *payment* are ordinary financial vocabulary that reads as a metric question to a
        # classifier tuned on 10-K line items. Db hits per answer went from 38,584 to
        # 6,600,197 at SF1000 — the AllNodesScan signature — and accuracy to 0%. The synthetic
        # phrasing had been avoiding the trigger by accident.
        #
        # ``_is_legal_issue_question`` immediately below already guards itself this way; this
        # function simply never did.
        if "FinancialMetric" not in self.ontology.nodes:
            return False
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
