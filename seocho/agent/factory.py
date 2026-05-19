from __future__ import annotations

from typing import Any, Optional


def indexing_system_prompt(ontology: Any) -> str:
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


def query_system_prompt(ontology: Any) -> str:
    ctx = ontology.to_query_context()
    return f"""You are a query agent for a knowledge graph. Your job is to
answer questions by building and executing graph queries.

**Ontology: {ctx.get('ontology_name', 'unknown')}**

{ctx.get('graph_schema', '')}

## Workflow (ADR-0090 tiered NL→Cypher)

1. Analyze the user's question to determine intent:
   - entity_lookup, relationship_lookup, neighbors, path, count, list_all

2. **Tier 1 — template lookup (cheap, deterministic).**
   Call `text2cypher` (a.k.a. cypher_template_lookup) with the structured
   intent. If it returns a non-empty Cypher template, go to Tier-validate.

3. **Tier 2 — schema-grounded generation (only on Tier-1 miss).**
   - Call `similar_query_search` to retrieve up to k validated past
     (NL, Cypher) examples for this workspace as few-shot context.
   - Call `schema_introspect` to read the live workspace schema
     (labels / relationship types / property keys).
   - Use those signals to assemble a Cypher plan grounded in the actual
     workspace state. Never hallucinate labels or properties.

4. **Tier-validate.** Always call `validate_cypher` before
   `execute_cypher`. Pass the cypher, parameters, and the allow-lists you
   read from `schema_introspect`. If `ok=false`, fix the listed
   violations and re-validate once.

5. **Tier-execute.** Call `execute_cypher`.

6. **Tier 3 — single repair on validate or execute error.** If the
   previous step failed, feed the error text back and regenerate the
   Cypher once. After two failures total, return a clear refusal with
   the diagnostic — do not loop further.

7. Synthesize a concise answer from the records.

## Query hints

{ctx.get('query_hints', '')}

## Rules

- NEVER write Cypher directly without `text2cypher` or `validate_cypher`.
- `text2cypher` is preferred for known intents — it produces deterministic,
  ontology-grounded queries.
- `schema_introspect` is the source of truth for what labels / rels /
  properties exist in the active workspace.
- `validate_cypher` MUST run before any `execute_cypher`.
- Available node types: {ctx.get('node_types', '')}
- Available relationships: {ctx.get('relationship_types', '')}
- If no results found after the single Tier-3 repair, say so clearly.
"""


def supervisor_system_prompt(ontology: Any, routing_policy: Any = None) -> str:
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


def create_indexing_agent(
    *,
    ontology: Any,
    graph_store: Any,
    llm: Any,
    extraction_prompt: Any = None,
    ontology_context: Any = None,
    workspace_id: str = "default",
    model: Optional[str] = None,
    name: str = "IndexingAgent",
) -> Any:
    from agents import Agent, ModelSettings
    from ..tools import create_indexing_tools

    tools = create_indexing_tools(
        ontology=ontology,
        graph_store=graph_store,
        llm=llm,
        extraction_prompt=extraction_prompt,
        ontology_context=ontology_context,
        workspace_id=workspace_id,
    )
    return Agent(
        name=name,
        instructions=indexing_system_prompt(ontology),
        tools=tools,
        model=llm.to_agents_sdk_model(model=model),
        model_settings=ModelSettings(temperature=0.0),
    )


def create_query_agent(
    *,
    ontology: Any,
    graph_store: Any,
    llm: Any,
    vector_store: Any = None,
    ontology_context: Any = None,
    workspace_id: str = "default",
    model: Optional[str] = None,
    name: str = "QueryAgent",
) -> Any:
    from agents import Agent, ModelSettings
    from ..tools import create_query_tools

    tools = create_query_tools(
        ontology=ontology,
        graph_store=graph_store,
        vector_store=vector_store,
        ontology_context=ontology_context,
        workspace_id=workspace_id,
    )
    return Agent(
        name=name,
        instructions=query_system_prompt(ontology),
        tools=tools,
        model=llm.to_agents_sdk_model(model=model),
        model_settings=ModelSettings(temperature=0.1),
    )


def create_supervisor_agent(
    *,
    ontology: Any,
    graph_store: Any,
    llm: Any,
    vector_store: Any = None,
    extraction_prompt: Any = None,
    routing_policy: Any = None,
    ontology_context: Any = None,
    workspace_id: str = "default",
    model: Optional[str] = None,
    name: str = "Supervisor",
) -> Any:
    from agents import Agent, ModelSettings, handoff

    idx_agent = create_indexing_agent(
        ontology=ontology,
        graph_store=graph_store,
        llm=llm,
        extraction_prompt=extraction_prompt,
        ontology_context=ontology_context,
        workspace_id=workspace_id,
        model=model,
    )
    qry_agent = create_query_agent(
        ontology=ontology,
        graph_store=graph_store,
        llm=llm,
        vector_store=vector_store,
        ontology_context=ontology_context,
        workspace_id=workspace_id,
        model=model,
    )
    return Agent(
        name=name,
        instructions=supervisor_system_prompt(ontology, routing_policy=routing_policy),
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
        model=llm.to_agents_sdk_model(model=model),
        model_settings=ModelSettings(temperature=0.0),
    )
