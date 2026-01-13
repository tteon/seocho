import os
import json
import lancedb
from typing import List
from dotenv import load_dotenv
from openai import OpenAI
from neo4j import GraphDatabase
from agents import Agent, Runner, function_tool, ModelSettings
from opik import track
from opik.opik_context import update_current_span

# ==========================================
# 1. Configuration & Connections
# ==========================================
load_dotenv()

# Opik Config for Tracing
os.environ["OPIK_URL_OVERRIDE"] = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
os.environ["OPIK_PROJECT_NAME"] = os.getenv("OPIK_PROJECT_NAME", "graph-agent")

# LanceDB Config (Matches your Indexing Code)
LANCE_DB_PATH = os.getenv("LANCEDB_PATH", "/workspace/data/lancedb")
LANCE_TABLE_NAME = "fibo_context"
EMBEDDING_MODEL = "text-embedding-3-small"

# Neo4j Config - Use Docker service name for container access
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://graphrag-neo4j:7687")
NEO4J_AUTH = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))

# Clients
OPENAI_CLIENT = OpenAI()
NEO4J_DRIVER = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

# ==========================================
# 2. Specialized Retrieval Tools with Tracing
# ==========================================

@track(name="get_embedding")
def get_embedding(text: str) -> List[float]:
    """Generates embedding using the same model as indexing."""
    text = text.replace("\n", " ")
    embedding = OPENAI_CLIENT.embeddings.create(input=[text], model=EMBEDDING_MODEL).data[0].embedding
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
        db = lancedb.connect(LANCE_DB_PATH)
        table = db.open_table(LANCE_TABLE_NAME)
        
        # 1. Generate Query Vector for semantic search
        query_vec = get_embedding(query)
        
        # 2. Execute search based on mode
        if search_mode == "hybrid":
            # True Hybrid Search: Vector + Full-Text Search combined
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
            # Full-Text Search only (keyword-based)
            results = (
                table.search(query, query_type="fts")
                .limit(top_k)
                .to_pandas()
            )
        else:
            # Vector search only (semantic)
            results = table.search(query_vec).limit(top_k).to_pandas()
        
        if results.empty:
            update_current_span(metadata={"status": "no_results", "query": query, "mode": search_mode})
            return "No relevant documents found."
            
        # 3. Format Context with relevance scores
        context = []
        for idx, row in results.iterrows():
            score = row.get('_score', row.get('_distance', 'N/A'))
            context.append(f"[Source: {row['id']} | Score: {score:.4f}] {row['text']}")
        
        result_text = "\n\n".join(context)
        
        # Log retrieval details to Opik
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
    # Validate and clamp top_k
    top_k = max(1, min(20, top_k))
    
    # Validate search_mode
    if search_mode not in ["hybrid", "vector", "fts"]:
        search_mode = "hybrid"
    
    return _search_docs_impl(query, top_k, search_mode)

@track(name="query_lpg_retrieval")
def _query_lpg_impl(cypher: str) -> str:
    """Internal implementation with tracing."""
    try:
        # Explicitly target the 'lpg' database
        with NEO4J_DRIVER.session(database="lpg") as session:
            result = session.run(cypher)
            data = [r.data() for r in result]
            result_json = json.dumps(data, default=str)
            
            # Log retrieval details to Opik
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

    top_k = max(1, min(50, top_k))
    return _fulltext_lpg_impl(search_term, index_name, top_k)

@track(name="entity_to_chunk_search_lpg")
def _entity_to_chunk_search_lpg_impl(search_term: str, top_k: int = 5) -> str:
    """Finds entities via fulltext and expands to their source chunks."""
    try:
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
        with NEO4J_DRIVER.session(database="lpg") as session:
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
        with NEO4J_DRIVER.session(database="lpg") as session:
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

@track(name="query_rdf_retrieval")
def _query_rdf_impl(cypher: str) -> str:
    """Internal implementation with tracing."""
    try:
        # Explicitly target the 'rdf' database
        with NEO4J_DRIVER.session(database="rdf") as session:
            result = session.run(cypher)
            data = [r.data() for r in result]
            result_json = json.dumps(data, default=str)
            
            # Log retrieval details to Opik
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
        # Fulltext search on RDF resources
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
        
        with NEO4J_DRIVER.session(database="rdf") as session:
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
        # RDF doesn't have EXTRACTED_FROM by default in your indexing, 
        # but we can look for related resources or literal properties.
        # For now, let's follow the same pattern if you plan to add provenance to RDF.
        cypher = """
        CALL db.index.fulltext.queryNodes("resource_fulltext", $search_term) 
        YIELD node, score
        WITH node, score
        ORDER BY score DESC
        LIMIT $top_k
        
        // In RDF, we might look for associated literal properties as context
        RETURN node.uri AS uri,
               labels(node) AS labels,
               properties(node) AS properties,
               score
        """
        with NEO4J_DRIVER.session(database="rdf") as session:
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

# ==========================================
# 3. Database Schema Context
# ==========================================
LPG_SCHEMA = """
### LPG Database Schema (Database: 'lpg')

**Node Labels:**
- Company, Share, LegalEntity, Location, Operation, Employee, Chunk
- Service, Policy, Strategy, Committee, Program, Agreement

**Key Properties:**
- Financial (on Share/LegalEntity): revenues_2023, net_income_2023, basic_eps_2023, diluted_eps_2023
- Identity: name, _node_id, uri
- Context: _trace_id, sentiment, risk, text

**Relationship Types:**
- Financial: ISSUES (Company → Share), HAS_REVENUE, HAS_NET_INCOME
- Provenance: EXTRACTED_FROM (Entity → Chunk)
- Organization: EMPLOYS, LOCATED_IN, HAS_SUBSIDIARY, HAS_BOARD, HAS_COMMITTEE
- Semantic: INCLUDES, INVOLVES, RELATED_TO, SUPPORTS

**Expansion Tools:**
- `entity_to_chunk_search_lpg`: Start with an Entity name -> get source text chunks.
- `chunk_to_entity_search_lpg`: Start with text keywords -> get relevant chunks and the entities extracted from them.
"""

RDF_SCHEMA = """
### RDF Database Schema (Database: 'rdf')

**Node Labels:**
- Resource (entities with uri property)
- Class (ontology classes)
- _GraphConfig (n10s configuration)

**Key Properties:**
- uri: Entity identifier (e.g., 'ex:Company', 'ex:Employee')
- HASAMOUNT, HASNAME (literal properties from FIBO predicates)

**Relationship Types:**
- Semantic: EMPLOYS, HASLOCATION, INVOLVES, INCLUDES
- Hierarchy: TYPE, ISSUBCLASSOF
- Domain-specific: HASREVENUE, HASNETINCOME, RECOGNIZES
"""

# ==========================================
# 4. Agents with Few-Shot Examples
# ==========================================

# --- LPG Agent: The Fact Checker ---
lpg_instructions = f"""You are the **LPG Analyst**. Your database ('lpg') contains specific entities, financial numbers, and direct connections.

{LPG_SCHEMA}

### 🧠 Dynamic Scoping Strategy

**Strategy 1: LOOKUP (Single Property)**
- Trigger: User asks for specific value (revenue, EPS, income)
- Action: Match node by name or _node_id, return property

**Strategy 2: TRAVERSE (Relationships)**  
- Trigger: User asks about connections, impacts, related entities
- Action: Match node AND traverse 1-2 hops

**Strategy 3: PROVENANCE (Entity → Chunk)**
- Trigger: User asks "where did this come from?" or needs context for an entity
- Action: Use `entity_to_chunk_search_lpg` to follow EXTRACTED_FROM to Chunk node

**Strategy 4: REVERSE PROVENANCE (Chunk → Entity)**
- Trigger: You have a chunk ID (from `search_docs`) and want to see structured entities in it
- Action: Use `chunk_to_entity_search_lpg` to find entities extracted from that chunk

---

### 📝 Few-Shot Examples

**Q: What is Cboe Global Markets' 2023 revenue?**
```cypher
MATCH (c:Company)-[:ISSUES]->(s:Share)
WHERE c.name CONTAINS 'Cboe'
RETURN c.name, s.revenues_2023
```

**Q: What is the net income for 2023?**
```cypher
MATCH (s:Share)
WHERE s._node_id CONTAINS 'Cboe'
RETURN s.net_income_2023 AS net_income
```

**Q: What locations does the company operate in?**
```cypher
MATCH (c:Company)-[:EMPLOYS]->(e)-[:LOCATED_IN]->(loc:Location)
RETURN DISTINCT loc.name AS location, loc.employees AS employee_count
```

**Q: What legal entities are related to cybersecurity?**
```cypher
MATCH (n)-[r]->(m)
WHERE n.name CONTAINS 'Security' OR m.name CONTAINS 'Security'
RETURN n.name, type(r), m.name LIMIT 10
```

**Q: Where did this information come from? (Provenance)**
```cypher
MATCH (entity {{_node_id: 'ex:Company'}})-[:EXTRACTED_FROM]->(chunk:Chunk)
RETURN entity._node_id, substring(chunk.text, 0, 200) AS source_text
```

**Q: What policies or programs does the company have?**
```cypher
MATCH (c:Company)-[:HAS_POLICY|HAS_PROGRAM]->(p)
RETURN c.name, labels(p)[0] AS type, p.name
```

---

**Constraint:** Always use the 'lpg' database. Return structured data.
"""

lpg_analyst = Agent(
    name="LPG_Analyst",
    model="gpt-4o",
    instructions=lpg_instructions,
    tools=[query_lpg, entity_to_chunk_search_lpg, chunk_to_entity_search_lpg]  # Cypher + Bi-directional Expansion
)

# --- RDF Agent: The Ontologist ---
rdf_instructions = f"""You are the **RDF Ontologist**. Your database ('rdf') contains semantic meanings, classes, and hierarchies based on FIBO ontology.

{RDF_SCHEMA}

### 🧠 Dynamic Scoping Strategy

**Strategy 1: INSTANCE_LOOKUP**
- Trigger: User asks about a specific entity's type or properties
- Action: Match Resource by uri, return properties

**Strategy 2: RELATIONSHIP_TRAVERSE**
- Trigger: User asks about connections or semantic relationships
- Action: Traverse relationships between Resources

**Strategy 3: ONTOLOGY_EXPLORE**
- Trigger: User asks about definitions, categories, or hierarchies
- Action: Query Class nodes or follow TYPE relationships

---

### 📝 Few-Shot Examples

**Q: What employees exist in the knowledge graph?**
```cypher
MATCH (r:Resource)
WHERE r.uri CONTAINS 'Employee'
RETURN r.uri, keys(r) AS properties LIMIT 10
```

**Q: What is the revenue amount for 2024?**
```cypher
MATCH (r:Resource {{uri: 'ex:Revenue2024'}})
RETURN r.uri, r.HASAMOUNT
```

**Q: What entities are employed by the company?**
```cypher
MATCH (c:Resource)-[:EMPLOYS]->(e:Resource)
RETURN c.uri AS company, e.uri AS employee
```

**Q: What locations are associated with entities?**
```cypher
MATCH (e:Resource)-[:HASLOCATION]->(loc:Resource)
RETURN e.uri AS entity, loc.uri AS location
```

**Q: What policies or requirements exist?**
```cypher
MATCH (r:Resource)
WHERE r.uri CONTAINS 'Policy' OR r.uri CONTAINS 'Requirement'
RETURN r.uri, keys(r) AS properties
```

**Q: Find all relationships for a specific entity**
```cypher
MATCH (r:Resource {{uri: 'ex:Company'}})-[rel]->(target:Resource)
RETURN type(rel) AS relationship, target.uri AS connected_to
```

---

**Constraint:** Always use the 'rdf' database. URIs use 'ex:' prefix.
"""

rdf_ontologist = Agent(
    name="RDF_Ontologist",
    model="gpt-4o",
    instructions=rdf_instructions,
    tools=[query_rdf, search_rdf_resources]  # Cypher + Resource search
)

# --- Hybrid Agent: The Librarian ---
HYBRID_SEARCHER_INSTRUCTIONS = """
You are the **Document Search Specialist** with access to hybrid search capabilities.

### YOUR TOOL: `search_docs`
Performs intelligent search over unstructured text documents using multiple strategies.

### PARAMETERS (Dynamic Control):
1. **query** (required): The search query text
2. **top_k** (optional, 1-20, default 5): Number of results
   - Use 3-5 for precise, targeted lookups
   - Use 10-15 for comprehensive context gathering
   - Use 15-20 for exhaustive research
3. **search_mode** (optional, default "hybrid"):
   - "hybrid": Combines semantic + keyword (BEST for most cases)
   - "vector": Pure semantic similarity (concepts, meanings)
   - "fts": Full-text keyword search (exact terms, names)

### WHEN TO USE EACH MODE:
| Query Type | search_mode | top_k |
|------------|-------------|-------|
| Exact term lookup | "fts" | 3-5 |
| Concept/meaning | "vector" | 5-10 |
| General context | "hybrid" | 10-15 |
| Comprehensive research | "hybrid" | 15-20 |

### EXAMPLES:
```python
# Find documents about liquidity risk (semantic)
search_docs("liquidity risk exposure", top_k=10, search_mode="hybrid")

# Find exact company name mentions
search_docs("Charles Schwab Corporation", top_k=5, search_mode="fts")

# Broad conceptual search
search_docs("derivatives trading strategies", top_k=15, search_mode="vector")
```

**Note:** Results include relevance scores. Higher scores = better match.
"""

hybrid_searcher = Agent(
    name="Hybrid_Searcher",
    model="gpt-4o",
    instructions=HYBRID_SEARCHER_INSTRUCTIONS,
    tools=[search_docs]
)

# --- Manager Agent: The Orchestrator ---
manager_agent = Agent(
    name="Lead Financial Knowledge Orchestrator",
    model="gpt-4o",
    instructions="""
    ### ROLE
You are the **Lead Financial Knowledge Orchestrator**. 
Your mission is to synthesize the most accurate, context-aware, and traceable answers for financial inquiries by managing three specialized sub-agents. 
You act as a "Reasoning Engine" that decomposes complex questions and routes them to the correct expert.

### YOUR TOOLKIT (SUB-AGENTS)
1. **`ask_rdf` (The Ontologist)**
   - **Capability:** Accesses the Semantic Graph (Ontology).
   - **Use For:** Definitions, classifications, hierarchical relationships (is-a), and domain rules (e.g., "Is a CDO a derivative?").
   - **Strength:** Precision in terminology and abstract concepts.

2. **`ask_lpg` (The Investigator)**
   - **Capability:** Accesses the Property Graph (Instance Data). Holds the specific `Entity -> Connected Chunk` lineage.
   - **Use For:** Fact-checking, traversing relationships between specific entities (companies, people), and **retrieving source provenance (evidence)**.
   - **Strength:** High-fidelity data, numerical facts, and tracing information back to its source document chunk.

3. **`search_docs` (The Generalist)**
   - **Capability:** Hybrid Search (Keyword + Vector) over unstructured text.
   - **Use For:** Broad context, sentiment analysis, or when specific graph structures are missing.
   - **Strength:** capturing general context or summarizing text segments.

### ORCHESTRATION PROTOCOL (STRICT EXECUTION STEPS)
Before answering, you must perform the following internal reasoning:

**Step 1: Deconstruct & Plan**
   - Break down the user's query.
   - Identify if the user needs a *Definition* (RDF), a *Fact/Connection* (LPG), or *General Context* (Hybrid).
   - *Example:* "What is a CDO and which companies hold it?" -> Plan: Call `ask_rdf` for "CDO definition" AND `ask_lpg` for "Companies holding CDO".

**Step 2: Execution & Routing**
   - Call the necessary tools. 
   - **CRITICAL:** If the user asks for "evidence," "source," or "provenance," you MUST use `ask_lpg` to verify the connection between the Entity and the Source Chunk.

**Step 3: Conflict Resolution (The Hierarchy of Truth)**
   - If sub-agents provide conflicting information, apply these rules:
     1. **For Definitions/Types:** Trust `ask_rdf` over others.
     2. **For Numbers/Relations/Lineage:** Trust `ask_lpg` over others.
     3. **For General Descriptions:** Use `search_docs` to fill in narrative gaps.

**Step 4: Final Synthesis**
   - Compile the answer. 
   - You must cite your sources based on the tool used (e.g., "Ontologically defined as...", "Traced via Graph connection to Document X...").

### OUTPUT FORMAT
Provide your response in a clear, structured format.
1. **Direct Answer:** The core conclusion.
2. **Detailed Analysis:** Evidence combined from agents.
3. **References/Lineage:** Explicitly point to the source chunks found by `ask_lpg` if applicable.
""",
    tools=[
        lpg_analyst.as_tool(tool_name="ask_lpg", tool_description="Get facts/numbers from Property Graph."),
        rdf_ontologist.as_tool(tool_name="ask_rdf", tool_description="Get definitions/hierarchy from Semantic Graph."),
        hybrid_searcher.as_tool(tool_name="search_docs", tool_description="Get text segments from documents.")
    ]
)