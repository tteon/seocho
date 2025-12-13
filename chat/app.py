import streamlit as st
import requests
import graphviz
from typing import List
import os

# Backend URL
API_URL = os.getenv("EXTRACTION_SERVICE_URL", "http://extraction-service:8001") + "/run_agent"
# API_URL = "http://158.247.253.65:8001/run_agent"

# ==========================================
# Visualization Function
# ==========================================
def render_graph(active_nodes: List[str]):
    """Draw graph based on trace_path"""
    graph = graphviz.Digraph()
    graph.attr(rankdir='LR', size='8,5')

    # Define Node Structure
    all_nodes = ["Router", "Supervisor", "GraphAgent", "VectorAgent", "WebSearchAgent"]
    
    for node in all_nodes:
        if node in active_nodes:
            # Active (Orange)
            graph.node(node, style='filled', fillcolor='#ff9f43', color='#e67e22', fontcolor='white', shape='box')
        else:
            # Inactive (Grey)
            graph.node(node, style='dashed', color='#ecf0f1', fontcolor='#95a5a6', shape='box')

    # Draw Edges (Simplified Logic)
    if "Router" in active_nodes:
        if "Supervisor" in active_nodes:
            graph.edge("Router", "Supervisor", color="#e74c3c", penwidth="2")
            # Supervisor -> Workers
            for worker in ["GraphAgent", "VectorAgent", "WebSearchAgent"]:
                if worker in active_nodes:
                    graph.edge("Supervisor", worker, color="#3498db", penwidth="2")
        elif len(active_nodes) == 1:
            pass # Router only

    return graph

# ==========================================
# Main UI
# ==========================================
st.set_page_config(layout="wide", page_title="Agent Monitor")
st.title("🤖 Enterprise Agent System (GraphRAG)")

# Initialize Session State
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_trace" not in st.session_state:
    st.session_state.last_trace = []

# Layout: Chat (5) : Vis (7)
col_chat, col_vis = st.columns([5, 7])

# --- 1. Chat Window ---
with col_chat:
    st.subheader("💬 Chat")
    
    # Display History
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Input
    if prompt := st.chat_input("Start Extraction / Ask Question"):
        # Display User Params
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Call Backend
        with st.spinner("Agents are processing..."):
            try:
                # Trigger Extraction API
                payload = {
                    "query": prompt,
                    "user_id": "streamlit_user"
                }
                response = requests.post(API_URL, json=payload)
                
                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status", "Unknown")
                    
                    bot_reply = f"Pipeline Triggered! Status: {status}"
                    # Mock Trace for visualization demo
                    trace_path = ["Router", "Supervisor", "GraphAgent", "VectorAgent"]
                    
                    # Store Results
                    st.session_state.messages.append({"role": "assistant", "content": bot_reply})
                    st.session_state.last_trace = trace_path
                    
                    # Display Bot Reply
                    with st.chat_message("assistant"):
                        st.markdown(bot_reply)
                else:
                    st.error(f"Server Error: {response.text}")

            except Exception as e:
                st.error(f"Connection Failed: {e}")
                st.warning(f"Is Extraction Service running at {API_URL}?")

# --- 2. Visualization ---
with col_vis:
    st.subheader("🕸️ Active Agent Path")
    
    if st.session_state.last_trace:
        # Path Text
        st.success(f"Path: {' ➤ '.join(st.session_state.last_trace)}")
        
        # Graph Render
        chart = render_graph(st.session_state.last_trace)
        st.graphviz_chart(chart, use_container_width=True)
    else:
        st.info("Waiting for requests...")
        empty_chart = render_graph([]) # Empty Graph
        st.graphviz_chart(empty_chart, use_container_width=True)
