"""
LanceDB vector search tools for hybrid search over unstructured text.
"""
import json
from typing import List
from opik import track
from opik.opik_context import update_current_span
from agents import function_tool

from src.config.settings import LANCEDB_TABLE, EMBEDDING_MODEL
from src.retrieval.connections import get_lancedb, get_openai_client


@track(name="get_embedding")
def get_embedding(text: str) -> List[float]:
    """Generates embedding using OpenAI's embedding model."""
    client = get_openai_client()
    text = text.replace("\n", " ")
    embedding = client.embeddings.create(input=[text], model=EMBEDDING_MODEL).data[0].embedding
    update_current_span(metadata={"model": EMBEDDING_MODEL, "text_length": len(text)})
    return embedding


@track(name="search_docs_retrieval")
def _search_docs_impl(query: str, top_k: int = 5, search_mode: str = "hybrid") -> str:
    """
    Internal implementation with tracing.
    
    Args:
        query: Search query text
        top_k: Number of results to return (default 5)
        search_mode: "hybrid" (vector + FTS), "vector", or "fts" (full-text only)
    """
    try:
        db = get_lancedb()
        table = db.open_table(LANCEDB_TABLE)
        
        # Generate Query Vector for semantic search
        query_vec = get_embedding(query)
        
        # Execute search based on mode
        if search_mode == "hybrid":
            try:
                results = (
                    table.search(query, query_type="hybrid")
                    .vector(query_vec)
                    .limit(top_k)
                    .to_pandas()
                )
            except Exception:
                # Fallback to vector search if FTS index not available
                results = table.search(query_vec).limit(top_k).to_pandas()
                search_mode = "vector_fallback"
        elif search_mode == "fts":
            results = (
                table.search(query, query_type="fts")
                .limit(top_k)
                .to_pandas()
            )
        else:
            results = table.search(query_vec).limit(top_k).to_pandas()
        
        if results.empty:
            update_current_span(metadata={"status": "no_results", "query": query, "mode": search_mode})
            return "No relevant documents found."
        
        # Format Context with relevance scores
        context = []
        for idx, row in results.iterrows():
            score = row.get('_score', row.get('_distance', 'N/A'))
            context.append(f"[Source: {row['id']} | Score: {score:.4f}] {row['text']}")
        
        result_text = "\n\n".join(context)
        
        update_current_span(
            metadata={
                "retrieval_type": f"hybrid_search_{search_mode}",
                "database": "lancedb",
                "query": query,
                "top_k": top_k,
                "search_mode": search_mode,
                "num_results": len(results),
                "result_preview": result_text[:500]
            }
        )
        
        return result_text
    except Exception as e:
        update_current_span(metadata={"error": str(e), "query": query})
        return f"LanceDB Error: {str(e)}"


@function_tool
def search_docs(query: str, top_k: int = 5, search_mode: str = "hybrid") -> str:
    """
    Searches unstructured context using Hybrid Search (Vector + Full-Text).
    
    Args:
        query: The search query text
        top_k: Number of results to return (1-20, default 5). 
               Use higher values for broad searches, lower for precise lookups.
        search_mode: Search strategy - "hybrid" (best balance), "vector" (semantic only), 
                     or "fts" (keyword only). Default is "hybrid".
    
    Use this for broad concepts, definitions, narrative context, or when graph data is missing.
    """
    top_k = max(1, min(20, top_k))
    if search_mode not in ["hybrid", "vector", "fts"]:
        search_mode = "hybrid"
    return _search_docs_impl(query, top_k, search_mode)
