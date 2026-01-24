
import asyncio
import os
from agents import Agent, Runner
from neo4j_trace_logger import Neo4jTraceLogger

# 1. Initialize Logger (Targeting 'agent_traces' DB)
logger = Neo4jTraceLogger(database="agent_traces")

async def main():
    agent = Agent(name="GraphReasoningAgent", instructions="You are a helpful assistant.")
    
    user_query = "Creating a knowledge graph about AI."
    print(f"User Query: {user_query}")
    
    # 2. Log Start of Trace
    trace_id = logger.log_trace("Graph Creation Demo", user_query)
    
    # Simulate Step 1: Agent Thought/Action
    print("Agent is thinking...")
    logger.log_step(trace_id, agent.name, "THOUGHT", "I need to define entities for AI.", 
                   metadata={"model": "gpt-4", "token_usage": 45})
    
    # Run Agent (Actual execution)
    result = await Runner.run(agent, user_query)
    
    # 3. Log Result
    print(f"Agent Output: {result.final_output}")
    logger.log_step(trace_id, agent.name, "GENERATION", result.final_output,
                   metadata={"model": "gpt-4", "token_usage": 150, "latency_ms": 1200})
    
    print("\n✅ Trace logged to Neo4j database 'agent_traces'.")
    print("   Check NeoDash to visualize!")
    
    logger.close()

if __name__ == "__main__":
    asyncio.run(main())
