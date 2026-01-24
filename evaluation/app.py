
import streamlit as st
import sys
import os
import uuid
import json
import requests
from datetime import datetime

# Add parent dir to path
sys.path.append(os.path.abspath(".."))

from streamlit_flow import streamlit_flow
from streamlit_flow.elements import StreamlitFlowNode, StreamlitFlowEdge
from streamlit_flow.state import StreamlitFlowState
from streamlit_flow.layouts import TreeLayout

st.set_page_config(page_title="Seocho Agent Tracing", layout="wide", initial_sidebar_state="collapsed")

# --- Styles ---
st.markdown("""
<style>
    .stChatInput {
        position: fixed;
        bottom: 2rem;
        z-index: 100;
    }
    .block-container {
        padding-top: 2rem;
        padding-bottom: 5rem;
    }
    h1 {
        font-family: 'Inter', sans-serif;
        font-weight: 700;
        color: #1E1E1E;
    }
    .stChatMessage {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 10px;
    }
</style>
""", unsafe_allow_html=True)

# --- Configuration ---
API_URL = os.getenv("EXTRACTION_SERVICE_URL", "http://extraction-service:8001")

# --- Helper Functions ---

def get_trace_flow(steps):
    """
    Converts a list of step dictionaries into a StreamlitFlowState.
    """
    if not steps:
        return StreamlitFlowState(nodes=[], edges=[])

    nodes = []
    edges = []
    
    for i, s in enumerate(steps):
        # Determine Node Style based on Step Type
        node_type = s.get("type", "UNKNOWN")
        style = {"background-color": "#ffffff", "border": "1px solid #ddd", "color": "#333", "width": "220px"}
        
        icon = "⚙️"
        if node_type == "USER_INPUT":
            style["background-color"] = "#e3f2fd" # Light Blue
            icon = "👤"
        elif node_type == "THOUGHT":
            style["background-color"] = "#fff3e0" # Light Orange
            icon = "🤔"
        elif node_type == "GENERATION":
            style["background-color"] = "#e8f5e9" # Light Green
            icon = "✅"
        elif node_type == "TOOL_RESULT":
            style["background-color"] = "#f3e5f5" # Light Purple
            icon = "🛠️"
        
        # Content Shortening
        content_preview = s.get("content", "")
        if len(content_preview) > 60:
            content_preview = content_preview[:60] + "..."

        agent_name = s.get("agent", "System")
        label = f"**{icon} {agent_name}**\n\n_{node_type}_\n\n{content_preview}"
        
        nodes.append(StreamlitFlowNode(
            id=s["id"],
            pos=(0, 0), # Layout will handle this
            data={"content": label},
            node_type="default",
            style=style,
            draggable=True
        ))

        # Create Edge to next step
        if i < len(steps) - 1:
             edges.append(StreamlitFlowEdge(
                id=f"{s['id']}-{steps[i+1]['id']}",
                source=s["id"],
                target=steps[i+1]["id"],
                animated=True,
                style={"stroke": "#999"}
            ))

    return StreamlitFlowState(nodes=nodes, edges=edges)


# --- Main Application ---
st.title("🤖 Seocho Agent Studio")

# Initialize Session State
if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "current_trace_steps" not in st.session_state:
    st.session_state["current_trace_steps"] = []
if "trace_version" not in st.session_state:
    st.session_state["trace_version"] = 0
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())

# Layout
col1, col2 = st.columns([1, 1], gap="large")

with col1:
    st.subheader("💬 Conversation")
    
    # Chat Container
    chat_container = st.container(height=600)
    
    with chat_container:
        for msg in st.session_state["messages"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
    
    # Input
    if prompt := st.chat_input("Enter your query..."):
        # Add user message
        st.session_state["messages"].append({"role": "user", "content": prompt})
        with chat_container:
            with st.chat_message("user"):
                st.markdown(prompt)

        # Process
        with chat_container:
            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                message_placeholder.markdown("Running...")
                
                try:
                    # Call API
                    payload = {"query": prompt, "user_id": st.session_state["session_id"]}
                    resp = requests.post(f"{API_URL}/run_agent", json=payload)
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        response_text = data.get("response", "")
                        trace_steps = data.get("trace_steps", [])
                        
                        message_placeholder.markdown(response_text)
                        
                        st.session_state["messages"].append({"role": "assistant", "content": response_text})
                        
                        # Update Trace View
                        st.session_state["current_trace_steps"] = trace_steps
                        st.session_state["trace_version"] += 1
                    else:
                        st.error(f"API Error: {resp.status_code} - {resp.text}")
                        
                except Exception as e:
                    st.error(f"Connection Error: {e}")

with col2:
    st.subheader("🕸️ Live Agent Flow")
    st.caption("Automatic visualization of 'openai-agents' execution.")
    
    if st.session_state["current_trace_steps"]:
        # Fetch Flow State
        flow_state = get_trace_flow(st.session_state["current_trace_steps"])
        
        # Render
        streamlit_flow(
            "trace_flow",
            flow_state,
            layout=TreeLayout(direction='down'),
            fit_view=True,
            height=600,
            enable_pane_menu=True,
            enable_node_menu=True,
            key=f"flow_{st.session_state['trace_version']}" 
        )
    else:
        st.info("Agent steps will appear here automatically after you send a message.")

