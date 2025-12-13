
import streamlit as st
import sys
import os
import uuid
import asyncio
from agents import Runner
from extraction.agents import manager_agent
from demos.common.neo4j_trace_logger import Neo4jTraceLogger

# Add parent dir to path
sys.path.append(os.path.abspath(".."))

# Try Import AdvancedSQLiteSession
try:
    from agents.extensions.memory import AdvancedSQLiteSession
except ImportError:
    from agents import AdvancedSQLiteSession

# Try Import Visualization
try:
    from agents.extensions.visualization import draw_graph
except ImportError:
    draw_graph = None

st.set_page_config(page_title="Seocho Evaluation", layout="wide")

# --- Sidebar: Visualization ---
st.sidebar.title("Agent Architecture")
if draw_graph:
    try:
        # Generate Graph
        graph = draw_graph(manager_agent)
        # Render
        st.sidebar.graphviz_chart(graph.source)
        st.sidebar.success("Architecture Visualized")
    except Exception as e:
        st.sidebar.error(f"Could not render graph: {e}")
else:
    st.sidebar.warning("Visualization module not found. Install 'openai-agents[viz]'.")

st.sidebar.markdown("---")
st.sidebar.info("**NeoDash**: [Open Dashboard](http://localhost:5005)")

# --- Main Interface ---
st.title("🤖 Agent Evaluation & Tracing")

# Initialize Session State
if "messages" not in st.session_state:
    st.session_state["messages"] = []

if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())
    # Initialize SQLite Session
    db_path = "data/conversations.db"
    st.session_state["agent_session"] = AdvancedSQLiteSession(
        session_id=st.session_state["session_id"],
        db_path=db_path,
        create_tables=True
    )
    # Initialize Neo4j Logger
    st.session_state["logger"] = Neo4jTraceLogger(database="agent_traces")

# Display Chat History
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat Input
if prompt := st.chat_input("Ask the Manager Agent..."):
    # Add user message
    st.session_state["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Process
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        message_placeholder.markdown("Running...")
        
        session_id = st.session_state["session_id"]
        logger = st.session_state["logger"]
        sqlite_session = st.session_state["agent_session"]
        
        # 1. Neo4j Trace Start
        trace_id = logger.log_trace(f"Chat-{session_id[:8]}", prompt)
        logger.log_step(trace_id, manager_agent.name, "USER_INPUT", prompt)
        
        try:
            # 2. Run Agent
            # Using asyncio.run for async call in Streamlit
            result = asyncio.run(Runner.run(manager_agent, prompt, session=sqlite_session))
            
            # 3. Store Usage
            asyncio.run(sqlite_session.store_run_usage(result))
            
            # 4. Get Metrics
            turn_usage_list = asyncio.run(sqlite_session.get_turn_usage())
            latest_usage = turn_usage_list[-1] if turn_usage_list else {}
            
            total_tokens = latest_usage.get("total_tokens", 0)
            
            # 5. Sync to Neo4j
            logger.log_step(trace_id, manager_agent.name, "GENERATION", result.final_output,
                           metadata={
                               "model": "gpt-4o",
                               "token_usage": total_tokens
                           })
            
            response = result.final_output
            message_placeholder.markdown(response)
            
            st.session_state["messages"].append({"role": "assistant", "content": response})
            
            # Metric Display
            st.caption(f"💰 Token Usage: {total_tokens}")
            
        except Exception as e:
            st.error(f"Error: {e}")
            logger.log_step(trace_id, "System", "ERROR", str(e))
