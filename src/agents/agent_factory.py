import os
from enum import Enum
from typing import List, Set, Optional
from agents import Agent, Runner

# Import from new modular structure
from src.retrieval.lpg_tools import query_lpg, entity_to_chunk_search_lpg, chunk_to_entity_search_lpg
from src.retrieval.rdf_tools import query_rdf, search_rdf_resources, entity_to_chunk_search_rdf
from src.retrieval.lancedb_tools import search_docs
from src.config.schemas import LPG_SCHEMA, RDF_SCHEMA, HYBRID_SEARCHER_INSTRUCTIONS

class ToolMode(Enum):
    LPG = "lpg"
    RDF = "rdf"
    HYBRID = "hybrid"

class AgentFactory:
    """Factory to create agents with different tool combinations for experiments."""
    
    @staticmethod
    def create_agent(
        modes: Set[ToolMode],
        name_suffix: str = "",
        enhanced_search: bool = True
    ) -> Agent:
        """
        Create an agent with specified tool combinations.
        """
        tools = []
        capabilities = []
        instructions_parts = []
        
        # Add LPG tools
        if ToolMode.LPG in modes:
            tools.append(query_lpg)
            if enhanced_search:
                tools.extend([entity_to_chunk_search_lpg, chunk_to_entity_search_lpg])
            capabilities.append("LPG database (structured facts, relationships, provenance)")
            instructions_parts.append(f"### LPG Capabilities\n{LPG_SCHEMA}")
        
        # Add RDF tools
        if ToolMode.RDF in modes:
            tools.extend([query_rdf, search_rdf_resources])
            if enhanced_search:
                tools.append(entity_to_chunk_search_rdf)
            capabilities.append("RDF database (ontology, semantics, hierarchies)")
            instructions_parts.append(f"### RDF Capabilities\n{RDF_SCHEMA}")
        
        # Add Hybrid tools
        if ToolMode.HYBRID in modes:
            tools.append(search_docs)
            capabilities.append("Hybrid search (semantic + keyword over unstructured text)")
            instructions_parts.append(f"### Hybrid Search Capabilities\n{HYBRID_SEARCHER_INSTRUCTIONS}")
        
        # Generate dynamic instructions
        mode_str = "+".join([m.value.upper() for m in sorted(list(modes), key=lambda x: x.value)])
        agent_name = f"Agent_{mode_str}{name_suffix}"
        
        full_instructions = f"""You are the **{agent_name}**. 
Your mission is to provide accurate, context-aware answers using your available tools.

### Your Capabilities:
{chr(10).join(f"- {cap}" for cap in capabilities)}

{chr(10).join(instructions_parts)}

### Orchestration Protocol
1. **Deconstruct**: Break down the query into components (facts, definitions, context).
2. **Dynamic Scoping (Expansion)**: 
   - If you find an **Entity**, use `entity_to_chunk_search` to find its source context.
   - If you find a **Chunk** (via `search_docs`), use `chunk_to_entity_search` to find structured entities within it.
3. **Route**: Use the most appropriate tool for each component.
4. **Synthesize**: Combine information from all sources.
5. **Cite**: Explicitly mention which source (LPG, RDF, or Hybrid) provided each piece of information.

**Constraint:** Always use the correct database for each tool.
"""
        
        return Agent(
            name=agent_name,
            model="gpt-4o",
            instructions=full_instructions,
            tools=tools
        )

    @staticmethod
    def create_manager_agent(modes: Set[ToolMode]) -> Agent:
        """
        Create a Manager agent that orchestrates sub-agents based on the selected modes.
        """
        sub_agents = []
        
        if ToolMode.LPG in modes:
            lpg_agent = AgentFactory.create_agent({ToolMode.LPG}, name_suffix="_Sub")
            sub_agents.append(lpg_agent.as_tool(tool_name="ask_lpg", tool_description="Get facts/numbers from Property Graph."))
            
        if ToolMode.RDF in modes:
            rdf_agent = AgentFactory.create_agent({ToolMode.RDF}, name_suffix="_Sub")
            sub_agents.append(rdf_agent.as_tool(tool_name="ask_rdf", tool_description="Get definitions/hierarchy from Semantic Graph."))
            
        if ToolMode.HYBRID in modes:
            hybrid_agent = AgentFactory.create_agent({ToolMode.HYBRID}, name_suffix="_Sub")
            sub_agents.append(hybrid_agent.as_tool(tool_name="search_docs", tool_description="Get text segments from documents."))

        manager_instructions = """
You are the **Lead Financial Knowledge Orchestrator**. 
Your mission is to synthesize accurate answers by managing specialized sub-agents.

### Orchestration Protocol
1. **Plan**: Identify if the user needs a Definition (RDF), a Fact (LPG), or General Context (Hybrid).
2. **Execute**: Call the necessary sub-agents.
3. **Resolve**: If sources conflict, follow the Hierarchy of Truth:
   - Definitions/Types: Trust RDF.
   - Numbers/Relations/Lineage: Trust LPG.
   - General Descriptions: Use Hybrid Search.
4. **Synthesize**: Compile the final answer with citations.
"""
        
        return Agent(
            name="Orchestrator_" + "_".join([m.value.upper() for m in sorted(list(modes), key=lambda x: x.value)]),
            model="gpt-4o",
            instructions=manager_instructions,
            tools=sub_agents
        )
