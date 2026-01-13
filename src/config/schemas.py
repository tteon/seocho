"""
Database schema definitions for LPG and RDF databases.
Used by agents for context-aware query generation.
"""

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
"""
