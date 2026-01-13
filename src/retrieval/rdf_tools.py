"""
RDF (Resource Description Framework) query tools for Neo4j.
"""
import json
from opik import track
from opik.opik_context import update_current_span
from agents import function_tool

from src.config.settings import RDF_DATABASE
from src.retrieval.connections import get_neo4j_driver


@track(name="query_rdf_retrieval")
def _query_rdf_impl(cypher: str) -> str:
    """Internal implementation with tracing."""
    try:
        driver = get_neo4j_driver()
        with driver.session(database=RDF_DATABASE) as session:
            result = session.run(cypher)
            data = [r.data() for r in result]
            result_json = json.dumps(data, default=str)
            
            update_current_span(
                metadata={
                    "retrieval_type": "cypher_query",
                    "database": "rdf",
                    "cypher": cypher,
                    "num_results": len(data),
                    "result_preview": result_json[:500]
                }
            )
            
            return result_json
    except Exception as e:
        update_current_span(metadata={"error": str(e), "cypher": cypher, "database": "rdf"})
        return f"Neo4j (RDF) Error: {str(e)}"


@function_tool
def query_rdf(cypher: str) -> str:
    """
    Executes Cypher on the 'rdf' database.
    Use for: Ontology, type hierarchies (IS_A), and semantic definitions.
    """
    return _query_rdf_impl(cypher)


@track(name="fulltext_rdf_retrieval")
def _fulltext_rdf_impl(search_term: str, index_name: str = "resource_fulltext", top_k: int = 10) -> str:
    """Internal implementation for RDF fulltext search with tracing."""
    try:
        driver = get_neo4j_driver()
        cypher = f"""
        CALL db.index.fulltext.queryNodes("{index_name}", $search_term) 
        YIELD node, score
        RETURN node.uri AS uri,
               labels(node) AS labels,
               keys(node) AS properties,
               score
        ORDER BY score DESC
        LIMIT $top_k
        """
        
        with driver.session(database=RDF_DATABASE) as session:
            result = session.run(cypher, search_term=search_term, top_k=top_k)
            data = [r.data() for r in result]
            result_json = json.dumps(data, default=str)
            
            update_current_span(
                metadata={
                    "retrieval_type": "fulltext_search",
                    "database": "rdf",
                    "index_name": index_name,
                    "search_term": search_term,
                    "top_k": top_k,
                    "num_results": len(data)
                }
            )
            
            return result_json
    except Exception as e:
        update_current_span(metadata={"error": str(e), "search_term": search_term, "database": "rdf"})
        return f"Neo4j RDF Fulltext Error: {str(e)}"


@function_tool
def search_rdf_resources(search_term: str, index_name: str = "resource_fulltext", top_k: int = 10) -> str:
    """
    Search for RDF resources (Ontology terms, Classes, Instances) by keywords.
    Use this to find the correct URIs or definitions for concepts.
    """
    top_k = max(1, min(50, top_k))
    return _fulltext_rdf_impl(search_term, index_name, top_k)


@track(name="entity_to_chunk_search_rdf")
def _entity_to_chunk_search_rdf_impl(search_term: str, top_k: int = 5) -> str:
    """Internal implementation for RDF entity-to-chunk expansion."""
    try:
        driver = get_neo4j_driver()
        cypher = """
        CALL db.index.fulltext.queryNodes("resource_fulltext", $search_term) 
        YIELD node, score
        WITH node, score
        ORDER BY score DESC
        LIMIT $top_k
        
        // In RDF, we look for associated literal properties as context
        RETURN node.uri AS uri,
               labels(node) AS labels,
               properties(node) AS properties,
               score
        """
        with driver.session(database=RDF_DATABASE) as session:
            result = session.run(cypher, search_term=search_term, top_k=top_k)
            data = [r.data() for r in result]
            result_json = json.dumps(data, default=str)
            
            update_current_span(
                metadata={
                    "retrieval_type": "rdf_expansion",
                    "database": "rdf",
                    "search_term": search_term,
                    "num_results": len(data)
                }
            )
            return result_json
    except Exception as e:
        update_current_span(metadata={"error": str(e), "search_term": search_term})
        return f"RDF Expansion Error: {str(e)}"


@function_tool
def entity_to_chunk_search_rdf(search_term: str, top_k: int = 5) -> str:
    """
    Finds RDF resources and expands to their semantic context.
    Use this for: Deep semantic lookups and finding related ontology terms.
    """
    return _entity_to_chunk_search_rdf_impl(search_term, top_k)
