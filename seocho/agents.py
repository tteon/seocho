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


# ======================================================================
# Supervisor agent with hand-off
# ======================================================================

def _supervisor_system_prompt(ontology: Any, routing_policy: Any = None) -> str:
    """Build the supervisor agent's system prompt."""
    ctx = ontology.to_query_context()

    policy_section = ""
    if routing_policy is not None:
        policy_ctx = routing_policy.to_prompt_context()
        hints = routing_policy.to_agent_hints()
        policy_section = f"""
## Routing policy

{policy_ctx}

Derived settings:
- Quality threshold: {hints.get('extraction_quality_threshold', 'default')}
- Reasoning mode: {'enabled' if hints.get('reasoning_mode') else 'disabled'}
- Repair budget: {hints.get('repair_budget', 0)} attempts
- Validation: {hints.get('validation_on_fail', 'warn')}
- Answer style: {hints.get('answer_style', 'concise')}

When latency is dominant: prefer pipeline, skip retries, minimal validation.
When information_quality is dominant: use agent tools, enable retries, strict validation.
When token_efficiency is dominant: single-pass extraction, skip linking, concise answers.
"""

    return f"""You are a supervisor agent for a knowledge graph system.
Your job is to route user requests to the right specialist agent.

**Ontology: {ctx.get('ontology_name', 'unknown')}**

You have two specialist agents:
- **IndexingAgent**: For adding/indexing content into the knowledge graph.
  Hand off when the user provides text to store, documents to index,
  or facts to add.
- **QueryAgent**: For answering questions from the knowledge graph.
  Hand off when the user asks a question, requests information, or
  wants to query the graph.

## Routing rules

1. If the message contains text/data to index → hand off to IndexingAgent
2. If the message asks a question → hand off to QueryAgent
3. If ambiguous, ask the user to clarify
4. After a hand-off completes, summarize the result to the user
{policy_section}"""


def create_supervisor_agent(
    *,
    ontology: Any,
    graph_store: Any,
    llm: Any,
    vector_store: Any = None,
    extraction_prompt: Any = None,
    routing_policy: Any = None,
    model: Optional[str] = None,
    name: str = "Supervisor",
) -> Any:
    """Create a supervisor agent that routes to IndexingAgent or QueryAgent.

    The supervisor uses OpenAI Agents SDK hand-off to delegate work.
    Users just call ``Runner.run(supervisor, message)`` and the
    supervisor automatically routes to the right agent.

    Parameters
    ----------
    ontology:
        The Ontology driving both indexing and querying.
    graph_store:
        GraphStore for Neo4j/DozerDB.
    llm:
        LLMBackend for all agents.
    vector_store:
        Optional VectorStore.
    extraction_prompt:
        Optional custom PromptTemplate.
    model:
        Override model name.
    name:
        Supervisor agent name.

    Returns
    -------
    An ``agents.Agent`` with hand-offs to IndexingAgent and QueryAgent.

    Usage::

        supervisor = create_supervisor_agent(
            ontology=onto, graph_store=store, llm=llm,
        )
        result = await Runner.run(supervisor, "Samsung CEO is Jay Y. Lee")
        # → automatically routes to IndexingAgent

        result = await Runner.run(supervisor, "Who is Samsung's CEO?")
        # → automatically routes to QueryAgent
    """
    from agents import Agent, ModelSettings, handoff

    idx_agent = create_indexing_agent(
        ontology=ontology,
        graph_store=graph_store,
        llm=llm,
        extraction_prompt=extraction_prompt,
        model=model,
    )

    qry_agent = create_query_agent(
        ontology=ontology,
        graph_store=graph_store,
        llm=llm,
        vector_store=vector_store,
        model=model,
    )

    agent_model = llm.to_agents_sdk_model(model=model)
    system = _supervisor_system_prompt(ontology, routing_policy=routing_policy)

    return Agent(
        name=name,
        instructions=system,
        handoffs=[
            handoff(
                idx_agent,
                tool_description_override=(
                    "Route to the IndexingAgent for adding or indexing "
                    "content into the knowledge graph."
                ),
            ),
            handoff(
                qry_agent,
                tool_description_override=(
                    "Route to the QueryAgent for answering questions "
                    "or querying the knowledge graph."
                ),
            ),
        ],
        model=agent_model,
        model_settings=ModelSettings(temperature=0.0),
    )
