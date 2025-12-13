
import chainlit as cl
import sys
import os
import uuid

# Add parent dir to path to find packages if running from chat/
sys.path.append(os.path.abspath(".."))

from agents import Runner
# Import AdvancedSQLiteSession (Handling potential import path differences)
try:
    from agents.extensions.memory import AdvancedSQLiteSession
except ImportError:
    # Fallback to standard if extension not found (or handle error)
    print("⚠️ AdvancedSQLiteSession not found in extensions, checking root...")
    from agents import AdvancedSQLiteSession

from extraction.agents import manager_agent
from demos.common.neo4j_trace_logger import Neo4jTraceLogger

# Initialize Logger (Persistent for the app session)
logger = Neo4jTraceLogger(database="agent_traces")

@cl.on_chat_start
async def start():
    # Generate a persistent session ID for SQLite
    session_id = str(uuid.uuid4())
    cl.user_session.set("session_id", session_id)
    
    # Initialize Advanced Session
    db_path = "data/conversations.db"
    sqlite_session = AdvancedSQLiteSession(
        session_id=session_id,
        db_path=db_path,
        create_tables=True
    )
    cl.user_session.set("agent_session", sqlite_session)
    
    # Send welcome
    await cl.Message(content=f"**System Started.** Session ID: `{session_id}`\n\nI am the Manager Agent using `AdvancedSQLiteSession` for analytics.").send()

@cl.on_message
async def main(message: cl.Message):
    user_query = message.content
    session_id = cl.user_session.get("session_id")
    sqlite_session = cl.user_session.get("agent_session")
    
    # 1. Log Trace Start (Neo4j)
    trace_id = logger.log_trace(f"Chat-{session_id[:8]}", user_query)
    
    # Log User Input Step
    logger.log_step(trace_id, manager_agent.name, "USER_INPUT", user_query)
    
    # Show loading
    msg = cl.Message(content="")
    await msg.send()
    
    # 2. Run Agent with SQLite Session
    try:
        result = await Runner.run(manager_agent, user_query, session=sqlite_session)
        
        # 3. Store Usage Data (Crucial Step!)
        await sqlite_session.store_run_usage(result)
        
        # 4. Retrieve Rich Metrics
        # Get usage for the current turn (last one)
        turn_usage_list = await sqlite_session.get_turn_usage()
        latest_usage = turn_usage_list[-1] if turn_usage_list else {}
        
        # Extract metrics
        total_tokens = latest_usage.get("total_tokens", 0)
        input_tokens = latest_usage.get("input_tokens", 0)
        output_tokens = latest_usage.get("output_tokens", 0)
        
        print(f"💰 Token Usage: {total_tokens} (In: {input_tokens}, Out: {output_tokens})")

        # 5. Sync to Neo4j
        # We assume the result is the 'GENERATION' step or we log it as such
        logger.log_step(trace_id, manager_agent.name, "GENERATION", result.final_output, 
                       metadata={
                           "model": "gpt-4o", # Model info might be in result metadata
                           "token_usage": total_tokens,
                           "input_tokens": input_tokens,
                           "output_tokens": output_tokens,
                           "latency_ms": 0 # TODO: Calculate latency
                       })
        
        # Stream back to UI
        msg.content = result.final_output
        await msg.update()
        
    except Exception as e:
        error_msg = f"Error during execution: {str(e)}"
        await cl.Message(content=error_msg).send()
        logger.log_step(trace_id, "System", "ERROR", error_msg)

@cl.on_chat_end
def end():
    logger.close()
