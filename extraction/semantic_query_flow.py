"""
Semantic query flow for graph QA.

Implements a deterministic 4-agent orchestration:
1) Router agent
2) LPG agent
3) RDF agent
4) Answer generation agent

The semantic layer resolves question entities using fulltext search first,
then falls back to contains-based lookup, and applies lightweight
dedup/disambiguation scoring.
"""

from __future__ import annotations

import json
import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ontology_hints import OntologyHintStore

logger = logging.getLogger(__name__)


RDF_HINTS = {
    "rdf",
    "rdfs",
    "owl",
    "shacl",
    "sparql",
    "triple",
    "ontology",
    "uri",
    "class",
    "instance",
}

LPG_HINTS = {
    "cypher",
    "node",
    "edge",
    "path",
    "neighbor",
    "graph",
    "community",
    "relationship",
}

STOPWORDS = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "of",
    "to",
    "in",
    "on",
    "for",
    "and",
    "or",
    "do",
    "does",
    "did",
    "what",
    "which",
    "who",
    "whom",
    "where",
    "when",
    "why",
    "how",
    "tell",
    "show",
    "about",
    "please",
}

ENTITY_PROPERTIES = ("name", "title", "id", "uri", "code", "symbol", "alias")

QUESTION_LABEL_HINTS = {
    "company": {"company", "organization", "org", "enterprise", "firm"},
    "person": {"person", "human", "individual", "employee", "ceo", "founder"},
    "product": {"product", "service", "offering"},
    "event": {"event", "incident", "meeting"},
    "document": {"document", "section", "chunk"},
    "ontology": {"ontology", "class", "property", "concept"},
}


class SemanticEntityResolver:
    """Resolve question entities against graph entities."""

    def __init__(
        self,
        connector: Any,
        fulltext_index_hint: str = "entity_fulltext",
        candidate_limit: int = 5,
        ontology_hint_store: Optional[OntologyHintStore] = None,
    ):
        self.connector = connector
        self.fulltext_index_hint = fulltext_index_hint
        self.candidate_limit = candidate_limit
        self.ontology_hint_store = ontology_hint_store or OntologyHintStore(
            path=os.getenv("ONTOLOGY_HINTS_PATH", "output/ontology_hints.json")
        )

    def extract_question_entities(self, question: str) -> List[str]:
        """Extract candidate entity spans from user question."""
        quoted = [m.group(1).strip() for m in re.finditer(r'"([^"]+)"', question)]
        single_quoted = [m.group(1).strip() for m in re.finditer(r"'([^']+)'", question)]
        caps = [
            m.group(0).strip()
            for m in re.finditer(
                r"\b(?:[A-Z][a-zA-Z0-9&.-]{1,}|[A-Z]{2,})(?:\s+[A-Z][a-zA-Z0-9&.-]{1,})*\b",
                question,
            )
        ]

        entities: List[str] = []
        seen: Set[str] = set()
        for value in quoted + single_quoted + caps:
            cleaned = self._clean_span(value)
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen or key in STOPWORDS:
                continue
            seen.add(key)
            entities.append(cleaned)

        # Fallback: use long tokens when no span was detected.
        if not entities:
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9&._-]{2,}", question):
                key = token.lower()
                if key in STOPWORDS or key.isdigit():
                    continue
                if key in seen:
                    continue
                seen.add(key)
                entities.append(token)
                if len(entities) >= 3:
                    break
        return entities

    def resolve(self, question: str, databases: Sequence[str]) -> Dict[str, Any]:
        """Resolve entities for a question across one or more databases."""
        entities = self.extract_question_entities(question)
        label_hints = self._infer_label_hints(question)
        label_hints.update(self.ontology_hint_store.infer_label_hints(question))
        fulltext_indexes = self._discover_fulltext_indexes(databases)

        matches: Dict[str, List[Dict[str, Any]]] = {}
        unresolved: List[str] = []
        alias_resolved: Dict[str, str] = {}

        for entity in entities:
            resolved_text = self.ontology_hint_store.resolve_alias(entity)
            alias_resolved[entity] = resolved_text
            candidates: List[Dict[str, Any]] = []
            for db_name in databases:
                db_candidates = self._query_fulltext_candidates(
                    db_name=db_name,
                    entity_text=resolved_text,
                    indexes=fulltext_indexes.get(db_name, []),
                )
                if not db_candidates:
                    db_candidates = self._query_contains_candidates(
                        db_name=db_name,
                        entity_text=resolved_text,
                    )
                candidates.extend(db_candidates)

            ranked = self._rank_and_dedup(
                entity_text=entity,
                resolved_text=resolved_text,
                candidates=candidates,
                label_hints=label_hints,
            )
            if ranked:
                matches[entity] = ranked
            else:
                unresolved.append(entity)

        return {
            "entities": entities,
            "label_hints": sorted(label_hints),
            "alias_resolved": alias_resolved,
            "matches": matches,
            "unresolved_entities": unresolved,
            "ontology_hints": self.ontology_hint_store.to_summary(),
        }

    def _discover_fulltext_indexes(self, databases: Sequence[str]) -> Dict[str, List[str]]:
        by_db: Dict[str, List[str]] = {}
        for db_name in databases:
            indexes: List[str] = []
            candidates = [
                "SHOW FULLTEXT INDEXES YIELD name, state WHERE state = 'ONLINE' RETURN name",
                "SHOW INDEXES YIELD name, type, state WHERE type = 'FULLTEXT' AND state = 'ONLINE' RETURN name",
            ]
            for query in candidates:
                rows = self._run_query(db_name, query, params=None)
                if rows:
                    indexes = [str(row.get("name", "")).strip() for row in rows if row.get("name")]
                    if indexes:
                        break
            if self.fulltext_index_hint and self.fulltext_index_hint not in indexes:
                indexes.insert(0, self.fulltext_index_hint)
            by_db[db_name] = [idx for idx in indexes if idx]
        return by_db

    def _query_fulltext_candidates(
        self,
        db_name: str,
        entity_text: str,
        indexes: Sequence[str],
    ) -> List[Dict[str, Any]]:
        query = """
        CALL db.index.fulltext.queryNodes($index_name, $query)
        YIELD node, score
        RETURN elementId(node) AS node_id,
               labels(node) AS labels,
               coalesce(node.name, node.title, node.id, node.uri, elementId(node)) AS display_name,
               score
        ORDER BY score DESC
        LIMIT $limit
        """
        for index_name in indexes:
            rows = self._run_query(
                db_name,
                query,
                params={
                    "index_name": index_name,
                    "query": entity_text,
                    "limit": self.candidate_limit,
                },
            )
            if not rows:
                continue

            candidates: List[Dict[str, Any]] = []
            for row in rows:
                candidates.append(
                    {
                        "database": db_name,
                        "entity_text": entity_text,
                        "node_id": row.get("node_id"),
                        "labels": row.get("labels", []),
                        "display_name": row.get("display_name", ""),
                        "base_score": float(row.get("score", 0.0) or 0.0),
                        "source": "fulltext",
                        "index_name": index_name,
                    }
                )
            if candidates:
                return candidates
        return []

    def _query_contains_candidates(self, db_name: str, entity_text: str) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        WHERE any(key IN $properties
              WHERE n[key] IS NOT NULL
                AND toLower(toString(n[key])) CONTAINS toLower($query))
        RETURN elementId(n) AS node_id,
               labels(n) AS labels,
               coalesce(n.name, n.title, n.id, n.uri, elementId(n)) AS display_name
        LIMIT $limit
        """
        rows = self._run_query(
            db_name,
            query,
            params={
                "properties": list(ENTITY_PROPERTIES),
                "query": entity_text,
                "limit": self.candidate_limit,
            },
        )
        candidates: List[Dict[str, Any]] = []
        for row in rows:
            display_name = str(row.get("display_name", ""))
            lexical = self._lexical_similarity(entity_text, display_name)
            candidates.append(
                {
                    "database": db_name,
                    "entity_text": entity_text,
                    "node_id": row.get("node_id"),
                    "labels": row.get("labels", []),
                    "display_name": display_name,
                    "base_score": lexical,
                    "source": "contains",
                    "index_name": None,
                }
            )
        return candidates

    def _rank_and_dedup(
        self,
        entity_text: str,
        resolved_text: str,
        candidates: Sequence[Dict[str, Any]],
        label_hints: Set[str],
    ) -> List[Dict[str, Any]]:
        ranked: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, Any]] = set()

        normalized_entity = self._normalize(entity_text)
        normalized_resolved = self._normalize(resolved_text)
        for candidate in candidates:
            db_name = str(candidate.get("database", ""))
            node_id = candidate.get("node_id")
            dedup_key = (db_name, node_id)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            display_name = str(candidate.get("display_name", ""))
            normalized_display = self._normalize(display_name)
            lexical = self._lexical_similarity(normalized_entity, normalized_display)
            base_score = float(candidate.get("base_score", 0.0))
            label_boost = self._label_boost(candidate.get("labels", []), label_hints)
            exact_boost = 0.2 if normalized_entity == normalized_display else 0.0
            alias_boost = 0.12 if normalized_resolved == normalized_display else 0.0
            final_score = base_score + lexical + label_boost + exact_boost + alias_boost

            ranked.append(
                {
                    **candidate,
                    "lexical_score": round(lexical, 4),
                    "label_boost": round(label_boost, 4),
                    "alias_boost": round(alias_boost, 4),
                    "final_score": round(final_score, 4),
                }
            )

        ranked.sort(key=lambda item: item.get("final_score", 0.0), reverse=True)
        top_candidates = ranked[: self.candidate_limit]
        
        # UI Signal: If confidence gap > 0.15, mark as safe to auto-pin
        if len(top_candidates) > 0:
            best_score = top_candidates[0].get("final_score", 0.0)
            if len(top_candidates) > 1:
                runner_up = top_candidates[1].get("final_score", 0.0)
                gap = best_score - runner_up
                top_candidates[0]["is_confident"] = (gap > 0.15)
            else:
                top_candidates[0]["is_confident"] = True
                
        return top_candidates

    def _run_query(
        self,
        db_name: str,
        query: str,
        params: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        raw = self.connector.run_cypher(query=query, database=db_name, params=params)
        if isinstance(raw, str) and raw.startswith("Error"):
            logger.debug("Cypher error [%s]: %s", db_name, raw)
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            logger.debug("Non-JSON query output [%s]: %s", db_name, str(raw)[:160])
            return []
        if not isinstance(parsed, list):
            return []
        return parsed

    @staticmethod
    def _clean_span(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value.strip())
        cleaned = cleaned.strip(".,:;!?()[]{}")
        return cleaned

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    @staticmethod
    def _lexical_similarity(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    @staticmethod
    def _infer_label_hints(question: str) -> Set[str]:
        q = question.lower()
        hints: Set[str] = set()
        for _, tokens in QUESTION_LABEL_HINTS.items():
            if any(token in q for token in tokens):
                hints.update(tokens)
        return hints

    @staticmethod
    def _label_boost(labels: Sequence[str], label_hints: Set[str]) -> float:
        if not labels or not label_hints:
            return 0.0
        normalized_labels = {re.sub(r"[^a-z0-9]+", "", str(label).lower()) for label in labels}
        normalized_hints = {re.sub(r"[^a-z0-9]+", "", hint.lower()) for hint in label_hints}
        if normalized_labels & normalized_hints:
            return 0.15
        return 0.0


class QueryRouterAgent:
    """Route question to LPG, RDF, or hybrid path."""

    def route(self, question: str) -> str:
        q = question.lower()
        has_rdf = any(token in q for token in RDF_HINTS)
        has_lpg = any(token in q for token in LPG_HINTS)
        if has_rdf and has_lpg:
            return "hybrid"
        if has_rdf:
            return "rdf"
        return "lpg"


class LPGAgent:
    """LPG query agent with entity-aware neighborhood lookups."""

    def __init__(self, connector: Any, result_limit: int = 20):
        self.connector = connector
        self.result_limit = result_limit

    def run(
        self,
        question: str,
        databases: Sequence[str],
        semantic_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        top_matches = self._top_entity_matches(semantic_context)
        if not top_matches:
            return {
                "mode": "lpg",
                "summary": "No resolved entity. Returned graph label distribution.",
                "records": self._label_distribution(databases),
            }

        records: List[Dict[str, Any]] = []
        for item in top_matches:
            db_raw = item.get("database")
            node_id = item.get("node_id")
            if db_raw is None or node_id is None:
                continue
            db_name = str(db_raw)
            rows = self._neighbors_for_node(db_name, node_id)
            for row in rows:
                records.append(
                    {
                        "database": db_name,
                        "entity": row.get("entity"),
                        "labels": row.get("labels", []),
                        "neighbors": row.get("neighbors", []),
                    }
                )
        summary = "Resolved entities were expanded through LPG neighborhoods."
        return {"mode": "lpg", "summary": summary, "records": records}

    def _top_entity_matches(self, semantic_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        pairs: List[Dict[str, Any]] = []
        for entity, candidates in semantic_context.get("matches", {}).items():
            if not candidates:
                continue
            best = dict(candidates[0])
            best["question_entity"] = entity
            pairs.append(best)
        pairs.sort(key=lambda item: item.get("final_score", 0.0), reverse=True)
        return pairs[:3]

    def _neighbors_for_node(self, db_name: str, node_id: Any) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        WHERE elementId(n) = toString($node_id)
        OPTIONAL MATCH (n)-[r]-(m)
        RETURN coalesce(n.name, n.title, n.id, n.uri, elementId(n)) AS entity,
               labels(n) AS labels,
               collect(
                 DISTINCT {
                   type: type(r),
                   target: coalesce(m.name, m.title, m.id, m.uri, elementId(m)),
                   target_labels: labels(m)
                 }
               )[0..$limit] AS neighbors
        LIMIT 1
        """
        raw = self.connector.run_cypher(
            query=query,
            database=db_name,
            params={"node_id": node_id, "limit": self.result_limit},
        )
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        if isinstance(parsed, list):
            return parsed
        return []

    def _label_distribution(self, databases: Sequence[str]) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        RETURN labels(n)[0] AS label, count(*) AS count
        ORDER BY count DESC
        LIMIT 10
        """
        rows: List[Dict[str, Any]] = []
        for db_name in databases:
            raw = self.connector.run_cypher(query=query, database=db_name, params=None)
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, list):
                for row in parsed:
                    rows.append({"database": db_name, **row})
        return rows


class RDFAgent:
    """RDF-oriented query agent."""

    def __init__(self, connector: Any, result_limit: int = 20):
        self.connector = connector
        self.result_limit = result_limit

    def run(
        self,
        question: str,
        databases: Sequence[str],
        semantic_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        entities = semantic_context.get("entities", [])
        if entities:
            rows = self._resource_matches(databases, entities[0])
            if rows:
                return {
                    "mode": "rdf",
                    "summary": "Matched RDF-like resources using URI/name signals.",
                    "records": rows,
                }

        return {
            "mode": "rdf",
            "summary": "No RDF resource match found. Returned RDF label overview.",
            "records": self._rdf_label_overview(databases),
        }

    def _resource_matches(self, databases: Sequence[str], entity_text: str) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        WHERE (
            any(lbl IN labels(n) WHERE toLower(lbl) IN ['resource', 'class', 'ontology', 'individual'])
            OR n.uri IS NOT NULL
        )
          AND any(key IN ['uri', 'name', 'title', 'id']
              WHERE n[key] IS NOT NULL
                AND toLower(toString(n[key])) CONTAINS toLower($query))
        RETURN labels(n) AS labels,
               coalesce(n.uri, n.name, n.title, n.id, elementId(n)) AS resource,
               n.name AS name
        LIMIT $limit
        """
        all_rows: List[Dict[str, Any]] = []
        for db_name in databases:
            raw = self.connector.run_cypher(
                query=query,
                database=db_name,
                params={"query": entity_text, "limit": self.result_limit},
            )
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, list):
                for row in parsed:
                    all_rows.append({"database": db_name, **row})
        return all_rows

    def _rdf_label_overview(self, databases: Sequence[str]) -> List[Dict[str, Any]]:
        query = """
        MATCH (n)
        WHERE any(lbl IN labels(n) WHERE toLower(lbl) IN ['resource', 'class', 'ontology', 'individual'])
           OR n.uri IS NOT NULL
        RETURN labels(n)[0] AS label, count(*) AS count
        ORDER BY count DESC
        LIMIT 10
        """
        rows: List[Dict[str, Any]] = []
        for db_name in databases:
            raw = self.connector.run_cypher(query=query, database=db_name, params=None)
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, list):
                for row in parsed:
                    rows.append({"database": db_name, **row})
        return rows


class AnswerGenerationAgent:
    """Generate final response payload from routed agent outputs."""

    def synthesize(
        self,
        question: str,
        route: str,
        semantic_context: Dict[str, Any],
        lpg_result: Optional[Dict[str, Any]],
        rdf_result: Optional[Dict[str, Any]],
    ) -> str:
        entities = semantic_context.get("entities", [])
        unresolved = semantic_context.get("unresolved_entities", [])

        lines = [f"Route selected: {route.upper()}."]
        if entities:
            lines.append(f"Extracted entities: {', '.join(entities)}.")
        if unresolved:
            lines.append(f"Unresolved entities: {', '.join(unresolved)}.")

        if lpg_result and lpg_result.get("records"):
            lines.append(f"LPG records: {len(lpg_result['records'])}.")
        if rdf_result and rdf_result.get("records"):
            lines.append(f"RDF records: {len(rdf_result['records'])}.")
        if not ((lpg_result and lpg_result.get("records")) or (rdf_result and rdf_result.get("records"))):
            lines.append("No matching graph records were found for this question.")

        return " ".join(lines)


class SemanticAgentFlow:
    """Orchestrate semantic layer + router + specialist agents + answer agent."""

    def __init__(self, connector: Any):
        self.resolver = SemanticEntityResolver(connector)
        self.router = QueryRouterAgent()
        self.lpg_agent = LPGAgent(connector)
        self.rdf_agent = RDFAgent(connector)
        self.answer_agent = AnswerGenerationAgent()

    def run(
        self,
        question: str,
        databases: Sequence[str],
        entity_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        trace_steps: List[Dict[str, Any]] = []

        semantic_context = self.resolver.resolve(question, databases)
        self._apply_entity_overrides(semantic_context, entity_overrides or {})
        trace_steps.append(
            {
                "id": "0",
                "type": "SEMANTIC",
                "agent": "SemanticLayer",
                "content": "Entity extraction and disambiguation completed.",
                "metadata": {
                    "entities": semantic_context.get("entities", []),
                    "unresolved_entities": semantic_context.get("unresolved_entities", []),
                    "overrides_applied": sorted(
                        list(semantic_context.get("overrides_applied", {}).keys())
                    ),
                },
            }
        )

        route = self.router.route(question)
        trace_steps.append(
            {
                "id": "1",
                "type": "ROUTER",
                "agent": "RouterAgent",
                "content": f"Question routed to {route}.",
                "metadata": {"route": route},
            }
        )

        lpg_result: Optional[Dict[str, Any]] = None
        rdf_result: Optional[Dict[str, Any]] = None

        if route in {"lpg", "hybrid"}:
            lpg_result = self.lpg_agent.run(question, databases, semantic_context)
            trace_steps.append(
                {
                    "id": "2",
                    "type": "SPECIALIST",
                    "agent": "LPGAgent",
                    "content": lpg_result.get("summary", ""),
                    "metadata": {"records": len(lpg_result.get("records", []))},
                }
            )

        if route in {"rdf", "hybrid"}:
            rdf_result = self.rdf_agent.run(question, databases, semantic_context)
            trace_steps.append(
                {
                    "id": "3",
                    "type": "SPECIALIST",
                    "agent": "RDFAgent",
                    "content": rdf_result.get("summary", ""),
                    "metadata": {"records": len(rdf_result.get("records", []))},
                }
            )

        response = self.answer_agent.synthesize(
            question=question,
            route=route,
            semantic_context=semantic_context,
            lpg_result=lpg_result,
            rdf_result=rdf_result,
        )
        trace_steps.append(
            {
                "id": "4",
                "type": "GENERATION",
                "agent": "AnswerGenerationAgent",
                "content": response,
                "metadata": {},
            }
        )

        return {
            "response": response,
            "trace_steps": trace_steps,
            "route": route,
            "semantic_context": semantic_context,
            "lpg_result": lpg_result,
            "rdf_result": rdf_result,
        }

    @staticmethod
    def _apply_entity_overrides(
        semantic_context: Dict[str, Any],
        entity_overrides: Dict[str, Dict[str, Any]],
    ) -> None:
        if not entity_overrides:
            return

        matches = semantic_context.setdefault("matches", {})
        unresolved = set(semantic_context.get("unresolved_entities", []))
        applied: Dict[str, Dict[str, Any]] = {}

        for question_entity, override in entity_overrides.items():
            if not question_entity:
                continue

            db_name = override.get("database")
            node_id = override.get("node_id")
            if db_name is None or node_id is None:
                continue

            candidate = {
                "database": str(db_name),
                "entity_text": question_entity,
                "node_id": node_id,
                "labels": override.get("labels", []),
                "display_name": override.get("display_name", question_entity),
                "base_score": 1.0,
                "source": "override",
                "index_name": None,
                "lexical_score": 1.0,
                "label_boost": 0.0,
                "alias_boost": 0.0,
                "final_score": 10.0,
            }

            existing = matches.get(question_entity, [])
            matches[question_entity] = [candidate] + [
                row for row in existing
                if not (row.get("database") == candidate["database"] and row.get("node_id") == candidate["node_id"])
            ]
            unresolved.discard(question_entity)
            applied[question_entity] = {
                "database": candidate["database"],
                "node_id": candidate["node_id"],
                "display_name": candidate["display_name"],
            }

        semantic_context["unresolved_entities"] = sorted(unresolved)
        if applied:
            semantic_context["overrides_applied"] = applied
