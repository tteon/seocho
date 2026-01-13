"""
LPG (Labeled Property Graph) query tools for Neo4j.
"""
import json
from opik import track
from opik.opik_context import update_current_span
from agents import function_tool

from src.config.settings import LPG_DATABASE
from src.retrieval.connections import get_neo4j_driver


@track(name="query_lpg_retrieval")
def _query_lpg_impl(cypher: str) -> str:
    """Internal implementation with tracing."""
    try:
        driver = get_neo4j_driver()
        with driver.session(database=LPG_DATABASE) as session:
            result = session.run(cypher)
            data = [r.data() for r in result]
            result_json = json.dumps(data, default=str)
            
            update_current_span(
                metadata={
                    "retrieval_type": "cypher_query",
                    "database": "lpg",
                    "cypher": cypher,
                    "num_results": len(data),
                    "result_preview": result_json[:500]
                }
            )
            
            return result_json
    except Exception as e:
        update_current_span(metadata={"error": str(e), "cypher": cypher, "database": "lpg"})
        return f"Neo4j (LPG) Error: {str(e)}"


@function_tool
def query_lpg(cypher: str) -> str:
    """
    Executes Cypher on the 'lpg' database.
    Use for: Fact lookups, specific properties (revenue, dates), and direct relationships.
    """
    return _query_lpg_impl(cypher)


@track(name="entity_to_chunk_search_lpg")
def _entity_to_chunk_search_lpg_impl(search_term: str, top_k: int = 5) -> str:
    """Finds entities via fulltext and expands to their source chunks."""
    try:
        driver = get_neo4j_driver()
        cypher = """
        CALL db.index.fulltext.queryNodes("entity_fulltext", $search_term) 
        YIELD node, score
        WITH node, score
        WHERE NOT "Chunk" IN labels(node)
        OPTIONAL MATCH (node)-[:EXTRACTED_FROM]->(chunk:Chunk)
        RETURN node.name AS entity_name, 
               node._node_id AS entity_id,
               labels(node) AS entity_labels,
               collect({
                   text: chunk.text,
                   trace_id: chunk._trace_id
               }) AS source_chunks,
               score
        ORDER BY score DESC
        LIMIT $top_k
        """
        with driver.session(database=LPG_DATABASE) as session:
            result = session.run(cypher, search_term=search_term, top_k=top_k)
            data = [r.data() for r in result]
            return json.dumps(data, default=str)
    except Exception as e:
        update_current_span(metadata={"error": str(e)})
        return f"LPG Entity->Chunk Error: {str(e)}"


@function_tool
def entity_to_chunk_search_lpg(search_term: str, top_k: int = 5) -> str:
    """
    Search for ENTITIES (Companies, People, etc.) and get their source document context.
    Use this when you are looking for a specific entity and want to see the original text it was found in.
    """
    return _entity_to_chunk_search_lpg_impl(search_term, top_k)


@track(name="chunk_to_entity_search_lpg")
def _chunk_to_entity_search_lpg_impl(search_term: str, top_k: int = 5) -> str:
    """Finds chunks via fulltext and expands to entities extracted from them."""
    try:
        driver = get_neo4j_driver()
        cypher = """
        CALL db.index.fulltext.queryNodes("entity_fulltext", $search_term) 
        YIELD node, score
        WITH node, score
        WHERE "Chunk" IN labels(node)
        OPTIONAL MATCH (entity)-[:EXTRACTED_FROM]->(node)
        RETURN node.text AS chunk_text, 
               node._trace_id AS chunk_id,
               collect({
                   name: entity.name, 
                   id: entity._node_id, 
                   labels: labels(entity)
               }) AS related_entities,
               score
        ORDER BY score DESC
        LIMIT $top_k
        """
        with driver.session(database=LPG_DATABASE) as session:
            result = session.run(cypher, search_term=search_term, top_k=top_k)
            data = [r.data() for r in result]
            return json.dumps(data, default=str)
    except Exception as e:
        update_current_span(metadata={"error": str(e)})
        return f"LPG Chunk->Entity Error: {str(e)}"


@function_tool
def chunk_to_entity_search_lpg(search_term: str, top_k: int = 5) -> str:
    """
    Search for document CHUNKS by keywords and see what structured entities were extracted from them.
    Use this when you want to find relevant text segments and see the facts/entities associated with them.
    """
    return _chunk_to_entity_search_lpg_impl(search_term, top_k)
