"""
Agent definitions — IndexingAgent and QueryAgent using OpenAI Agents SDK.

These agents use tool-calling to orchestrate the indexing and query
pipelines. The LLM decides the execution flow (extract → validate →
score → link → write for indexing, text2cypher → execute → synthesize
for querying) while each step is deterministic.

Usage::

    from seocho.agents import create_indexing_agent, create_query_agent

    idx_agent = create_indexing_agent(ontology=onto, graph_store=store, llm=llm)
    qry_agent = create_query_agent(ontology=onto, graph_store=store, llm=llm)

Or via Session (recommended)::

    session = s.session("my_analysis")
    session.add("Samsung's CEO is Jay Y. Lee.")
    answer = session.ask("Who is Samsung's CEO?")
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ======================================================================
# System prompts
# ======================================================================

def _indexing_system_prompt(ontology: Any) -> str:
    """Build the indexing agent's system prompt from ontology."""
    ctx = ontology.to_query_context()
    return f"""You are an indexing agent for a knowledge graph. Your job is to
extract entities and relationships from text, validate them, and write them
to the graph database.

**Ontology: {ctx.get('ontology_name', 'unknown')}**

{ctx.get('graph_schema', '')}

## Workflow

1. Call `extract_entities` with the input text and category
2. Call `score_extraction` to check quality (target: >= 0.7)
   - If score < 0.5, call `extract_entities` again with a different approach
3. Call `validate_extraction` to check SHACL compliance
   - If validation fails, decide whether to fix or proceed
4. Call `link_entities` to deduplicate across chunks
5. Call `write_to_graph` to persist to the database

## Rules

- ALWAYS use the ontology types: {ctx.get('node_types', '')}
- ALWAYS use the ontology relationships: {ctx.get('relationship_types', '')}
- Re-extract if quality score is below threshold
- Report the final write result including node/relationship counts
"""


def _query_system_prompt(ontology: Any) -> str:
    """Build the query agent's system prompt from ontology."""
    ctx = ontology.to_query_context()
    return f"""You are a query agent for a knowledge graph. Your job is to
answer questions by building and executing graph queries.

**Ontology: {ctx.get('ontology_name', 'unknown')}**

{ctx.get('graph_schema', '')}

## Workflow

1. Analyze the user's question to determine intent:
   - entity_lookup: find info about a specific entity
   - relationship_lookup: find relationships between entities
   - neighbors: find connected entities
   - path: find paths between entities
   - count: count entities/relationships
   - list_all: list entities of a type

2. Call `text2cypher` with the structured intent (DO NOT write Cypher yourself)
3. Call `execute_cypher` with the generated query
4. If results are empty:
   - Try a broader query (e.g., neighbors instead of specific relationship)
   - Try fuzzy matching on entity names
5. Synthesize a clear answer from the results

## Query hints

{ctx.get('query_hints', '')}

## Rules

- NEVER write Cypher directly — always use `text2cypher`
- Available node types: {ctx.get('node_types', '')}
- Available relationships: {ctx.get('relationship_types', '')}
- If no results found after retries, say so clearly
"""


# ======================================================================
# Agent factories
# ======================================================================

def create_indexing_agent(
    *,
    ontology: Any,
    graph_store: Any,
    llm: Any,
    extraction_prompt: Any = None,
    model: Optional[str] = None,
    name: str = "IndexingAgent",
) -> Any:
    """Create an indexing agent with bound tools.

    Parameters
    ----------
    ontology:
        The Ontology that drives extraction prompts and validation.
    graph_store:
        GraphStore for writing to Neo4j/DozerDB.
    llm:
        LLMBackend (OpenAICompatibleBackend) for extraction calls.
    extraction_prompt:
        Optional custom PromptTemplate.
    model:
        Override model name for the agent's reasoning (defaults to llm.model).
    name:
        Agent name for tracing.

    Returns
    -------
    An ``agents.Agent`` instance ready for ``Runner.run()``.
    """
    from agents import Agent, ModelSettings
    from .tools import create_indexing_tools

    tools = create_indexing_tools(
        ontology=ontology,
        graph_store=graph_store,
        llm=llm,
        extraction_prompt=extraction_prompt,
    )

    agent_model = llm.to_agents_sdk_model(model=model)
    system = _indexing_system_prompt(ontology)

    return Agent(
        name=name,
        instructions=system,
        tools=tools,
        model=agent_model,
        model_settings=ModelSettings(temperature=0.0),
    )


def create_query_agent(
    *,
    ontology: Any,
    graph_store: Any,
    llm: Any,
    vector_store: Any = None,
    model: Optional[str] = None,
    name: str = "QueryAgent",
) -> Any:
    """Create a query agent with bound tools.

    Parameters
    ----------
    ontology:
        The Ontology for schema context.
    graph_store:
        GraphStore for executing Cypher.
    llm:
        LLMBackend for the agent's reasoning.
    vector_store:
        Optional VectorStore for similarity search.
    model:
        Override model name.
    name:
        Agent name for tracing.

    Returns
    -------
    An ``agents.Agent`` instance ready for ``Runner.run()``.
    """
    from agents import Agent, ModelSettings
    from .tools import create_query_tools

    tools = create_query_tools(
        ontology=ontology,
        graph_store=graph_store,
        vector_store=vector_store,
    )

    agent_model = llm.to_agents_sdk_model(model=model)
    system = _query_system_prompt(ontology)

    return Agent(
        name=name,
        instructions=system,
        tools=tools,
        model=agent_model,
        model_settings=ModelSettings(temperature=0.1),
    )
