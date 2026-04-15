"""
Agent tools — function_tool definitions for the OpenAI Agents SDK.

Each tool wraps one step of the indexing or query pipeline. The agent
decides when and how to call them; the tools are deterministic.

Tools are created via factory functions that close over the runtime
dependencies (ontology, graph_store, llm) so they're self-contained
when bound to an Agent.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _safe_json(text: str) -> Dict[str, Any]:
    """Parse JSON from LLM response, handling fenced code blocks."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines)
    return json.loads(text)


# ======================================================================
# Indexing tools
# ======================================================================

def make_extract_entities_tool(ontology: Any, llm: Any, extraction_prompt: Any = None):
    """Create an extract_entities tool bound to this ontology + LLM."""
    from agents import function_tool
    from .query.strategy import ExtractionStrategy

    strategy = ExtractionStrategy(ontology, extraction_prompt=extraction_prompt)

    @function_tool
    def extract_entities(text: str, category: str = "general") -> str:
        """Extract entities and relationships from text using the ontology-aware prompt.

        Args:
            text: The document text to extract entities from.
            category: Document category (general, finance, legal, medical, research).

        Returns:
            JSON string with extracted nodes and relationships.
        """
        strategy.category = category
        system, user = strategy.render(text)
        try:
            response = llm.complete(
                system=system,
                user=user,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            result = _safe_json(response.text)
            result["_usage"] = response.usage
            return json.dumps(result, default=str)
        except Exception as exc:
            logger.error("extract_entities failed: %s", exc)
            return json.dumps({"nodes": [], "relationships": [], "error": str(exc)})

    return extract_entities


def make_validate_extraction_tool(ontology: Any):
    """Create a validate_extraction tool bound to this ontology."""
    from agents import function_tool

    @function_tool
    def validate_extraction(extraction_json: str) -> str:
        """Validate extracted data against SHACL shapes derived from the ontology.

        Args:
            extraction_json: JSON string with nodes and relationships from extract_entities.

        Returns:
            JSON string with validation results (valid, errors list).
        """
        try:
            data = json.loads(extraction_json)
        except json.JSONDecodeError:
            return json.dumps({"valid": False, "errors": ["Invalid JSON input"]})

        errors = ontology.validate_with_shacl(data)
        return json.dumps({
            "valid": len(errors) == 0,
            "errors": errors,
            "nodes_count": len(data.get("nodes", [])),
            "relationships_count": len(data.get("relationships", [])),
        })

    return validate_extraction


def make_score_extraction_tool(ontology: Any):
    """Create a score_extraction tool bound to this ontology."""
    from agents import function_tool

    @function_tool
    def score_extraction(extraction_json: str) -> str:
        """Score the quality of extracted entities (0.0 to 1.0).

        Checks label matching, property completeness, and relationship validity
        against the ontology definition.

        Args:
            extraction_json: JSON string with nodes and relationships.

        Returns:
            JSON string with overall score and per-node scores.
        """
        try:
            data = json.loads(extraction_json)
        except json.JSONDecodeError:
            return json.dumps({"overall": 0.0, "error": "Invalid JSON input"})

        score_data = ontology.score_extraction(data)
        return json.dumps(score_data, default=str)

    return score_extraction


def make_write_to_graph_tool(
    graph_store: Any,
    ontology: Any = None,
    *,
    ontology_context: Any = None,
    workspace_id: str = "default",
):
    """Create a write_to_graph tool bound to this graph store."""
    from agents import function_tool

    @function_tool
    def write_to_graph(extraction_json: str, database: str = "neo4j", source_id: str = "") -> str:
        """Write extracted nodes and relationships to the graph database.

        Args:
            extraction_json: JSON string with nodes and relationships.
            database: Target Neo4j database name.
            source_id: Source identifier for tracking.

        Returns:
            JSON string with write summary (nodes_written, rels_written).
        """
        try:
            data = json.loads(extraction_json)
        except json.JSONDecodeError:
            return json.dumps({"ok": False, "error": "Invalid JSON input"})

        nodes = data.get("nodes", [])
        rels = data.get("relationships", [])
        if ontology_context is not None:
            from .ontology_context import apply_ontology_context_to_graph_payload

            nodes, rels = apply_ontology_context_to_graph_payload(nodes, rels, ontology_context)

        try:
            summary = graph_store.write(
                nodes,
                rels,
                database=database,
                source_id=source_id,
                workspace_id=workspace_id,
            )
            return json.dumps({
                "ok": True,
                "nodes_written": len(nodes),
                "relationships_written": len(rels),
                "database": database,
                "summary": str(summary) if summary else "",
            })
        except Exception as exc:
            logger.error("write_to_graph failed: %s", exc)
            return json.dumps({"ok": False, "error": str(exc)})

    return write_to_graph


def make_link_entities_tool(ontology: Any, llm: Any):
    """Create a link_entities (dedup) tool bound to this ontology + LLM."""
    from agents import function_tool
    from .query.strategy import LinkingStrategy

    linking = LinkingStrategy(ontology)

    @function_tool
    def link_entities(extraction_json: str) -> str:
        """Deduplicate and link entities across chunks.

        Merges nodes with the same label+name, resolves aliases.

        Args:
            extraction_json: JSON string with nodes and relationships.

        Returns:
            JSON string with deduplicated nodes and relationships.
        """
        try:
            data = json.loads(extraction_json)
        except json.JSONDecodeError:
            return json.dumps({"nodes": [], "relationships": [], "error": "Invalid JSON"})

        nodes = data.get("nodes", [])
        rels = data.get("relationships", [])

        # Cross-chunk dedup by label+name
        seen = {}
        merged_nodes = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            label = node.get("label", "")
            name = node.get("properties", {}).get("name", node.get("id", ""))
            key = f"{label}::{name}".lower()
            if key in seen:
                # Merge properties
                existing = seen[key]
                props = existing.get("properties", {})
                props.update(node.get("properties", {}))
                existing["properties"] = props
            else:
                seen[key] = node
                merged_nodes.append(node)

        return json.dumps({
            "nodes": merged_nodes,
            "relationships": rels,
            "deduplicated": len(nodes) - len(merged_nodes),
        }, default=str)

    return link_entities


# ======================================================================
# Query tools
# ======================================================================

def make_text2cypher_tool(ontology: Any):
    """Create a text2cypher tool that builds deterministic Cypher from intent."""
    from agents import function_tool
    from .query.cypher_builder import CypherBuilder

    builder = CypherBuilder(ontology)

    @function_tool
    def text2cypher(
        intent: str = "neighbors",
        anchor_entity: str = "",
        anchor_label: str = "",
        target_entity: str = "",
        target_label: str = "",
        relationship_type: str = "",
    ) -> str:
        """Build a deterministic Cypher query from structured intent.

        The LLM should NOT generate Cypher directly. Instead, provide the intent
        and entity information, and this tool will build correct Cypher.

        Args:
            intent: Query type — entity_lookup, relationship_lookup, neighbors, path, count, list_all.
            anchor_entity: The main entity name (e.g. "Samsung", "Apple").
            anchor_label: Node label for the anchor (e.g. "Company", "Person").
            target_entity: Target entity name for relationship queries.
            target_label: Node label for the target.
            relationship_type: Relationship type (e.g. "EMPLOYS", "INVESTED_IN").

        Returns:
            JSON string with cypher query and parameters.
        """
        try:
            cypher, params = builder.build(
                intent=intent,
                anchor_entity=anchor_entity,
                anchor_label=anchor_label,
                target_entity=target_entity,
                target_label=target_label,
                relationship_type=relationship_type,
            )
            return json.dumps({
                "cypher": cypher,
                "params": params,
                "intent": intent,
            }, default=str)
        except Exception as exc:
            logger.error("text2cypher failed: %s", exc)
            return json.dumps({"cypher": "", "params": {}, "error": str(exc)})

    return text2cypher


def make_execute_cypher_tool(
    graph_store: Any,
    *,
    ontology_context: Any = None,
    workspace_id: str = "default",
):
    """Create an execute_cypher tool bound to this graph store."""
    from agents import function_tool

    @function_tool
    def execute_cypher(cypher: str, params_json: str = "{}", database: str = "neo4j") -> str:
        """Execute a Cypher query against the graph database.

        Args:
            cypher: The Cypher query string.
            params_json: JSON string of query parameters.
            database: Target database name.

        Returns:
            JSON string with query results (list of records).
        """
        try:
            params = json.loads(params_json) if params_json else {}
        except json.JSONDecodeError:
            params = {}

        try:
            records = graph_store.query(cypher, params=params, database=database)
            payload = {
                "records": records,
                "count": len(records),
            }
            if ontology_context is not None:
                from .ontology_context import query_ontology_context_mismatch

                payload["ontology_context_mismatch"] = query_ontology_context_mismatch(
                    graph_store,
                    ontology_context,
                    workspace_id=workspace_id,
                    database=database,
                )
            return json.dumps(payload, default=str)
        except Exception as exc:
            logger.error("execute_cypher failed: %s", exc)
            return json.dumps({"records": [], "count": 0, "error": str(exc)})

    return execute_cypher


def make_search_similar_tool(vector_store: Any):
    """Create a search_similar tool bound to this vector store."""
    from agents import function_tool

    @function_tool
    def search_similar(query: str, limit: int = 5) -> str:
        """Find documents similar to the query using vector embeddings.

        Args:
            query: The search text.
            limit: Maximum number of results.

        Returns:
            JSON string with similar documents (id, text, score).
        """
        if vector_store is None:
            return json.dumps({"results": [], "error": "No vector store configured"})

        try:
            results = vector_store.search(query, limit=limit)
            return json.dumps({
                "results": [
                    {"id": r.id, "text": r.text[:500], "score": r.score}
                    for r in results
                ],
                "count": len(results),
            }, default=str)
        except Exception as exc:
            logger.error("search_similar failed: %s", exc)
            return json.dumps({"results": [], "count": 0, "error": str(exc)})

    return search_similar


# ======================================================================
# Tool collection factory
# ======================================================================

def create_indexing_tools(
    *,
    ontology: Any,
    graph_store: Any,
    llm: Any,
    extraction_prompt: Any = None,
    ontology_context: Any = None,
    workspace_id: str = "default",
) -> List[Any]:
    """Create the full set of indexing tools."""
    return [
        make_extract_entities_tool(ontology, llm, extraction_prompt),
        make_validate_extraction_tool(ontology),
        make_score_extraction_tool(ontology),
        make_link_entities_tool(ontology, llm),
        make_write_to_graph_tool(
            graph_store,
            ontology,
            ontology_context=ontology_context,
            workspace_id=workspace_id,
        ),
    ]


def create_query_tools(
    *,
    ontology: Any,
    graph_store: Any,
    vector_store: Any = None,
    ontology_context: Any = None,
    workspace_id: str = "default",
) -> List[Any]:
    """Create the full set of query tools."""
    tools = [
        make_text2cypher_tool(ontology),
        make_execute_cypher_tool(
            graph_store,
            ontology_context=ontology_context,
            workspace_id=workspace_id,
        ),
    ]
    if vector_store is not None:
        tools.append(make_search_similar_tool(vector_store))
    return tools
