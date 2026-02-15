
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
    .trace-detail-box {
        background-color: #fafafa;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 12px;
        font-size: 0.85rem;
        white-space: pre-wrap;
        word-break: break-word;
        max-height: 300px;
        overflow-y: auto;
    }
</style>
""", unsafe_allow_html=True)

# --- Configuration ---
API_URL = os.getenv("EXTRACTION_SERVICE_URL", "http://extraction-service:8001")

# --- Node Style Map ---
NODE_STYLES = {
    "USER_INPUT":   {"bg": "#e3f2fd", "border": "1px solid #90caf9",  "label": "User Input"},
    "THOUGHT":      {"bg": "#fff3e0", "border": "1px solid #ffcc80",  "label": "Thought"},
    "GENERATION":   {"bg": "#e8f5e9", "border": "1px solid #a5d6a7",  "label": "Generation"},
    "TOOL_RESULT":  {"bg": "#f3e5f5", "border": "1px solid #ce93d8",  "label": "Tool Result"},
    "SEMANTIC":     {"bg": "#e0f7fa", "border": "1px solid #00acc1",  "label": "Semantic"},
    "ROUTER":       {"bg": "#ede7f6", "border": "1px solid #7e57c2",  "label": "Router"},
    "SPECIALIST":   {"bg": "#f1f8e9", "border": "1px solid #7cb342",  "label": "Specialist"},
    "FANOUT":       {"bg": "#fff9c4", "border": "2px solid #fbc02d",  "label": "Fan-Out"},
    "DEBATE":       {"bg": "#bbdefb", "border": "2px solid #1976d2",  "label": "Agent"},
    "COLLECT":      {"bg": "#ffe0b2", "border": "2px solid #f57c00",  "label": "Collect"},
    "SYNTHESIS":    {"bg": "#c8e6c9", "border": "2px solid #388e3c",  "label": "Synthesis"},
    # Agent internal step types
    "TOOL_CALL":    {"bg": "#fff3e0", "border": "1px dashed #ef6c00",  "label": "Tool Call"},
    "TOOL_OUTPUT":  {"bg": "#f3e5f5", "border": "1px dashed #8e24aa",  "label": "Tool Output"},
    "REASONING":    {"bg": "#e8f5e9", "border": "1px dashed #2e7d32",  "label": "Reasoning"},
}

EDGE_COLORS = {
    "fanout":   "#1976d2",
    "internal": "#90a4ae",
    "collect":  "#f57c00",
    "synthesis": "#388e3c",
    "linear":   "#999",
}


# --- Helper Functions ---

def get_trace_flow(steps):
    """
    Converts a list of step dictionaries into a StreamlitFlowState.
    Supports linear (legacy), fan-out/collect (debate), and internal agent sub-steps.
    """
    if not steps:
        return StreamlitFlowState(nodes=[], edges=[])

    nodes = []
    edges = []

    for i, s in enumerate(steps):
        node_type = s.get("type", "UNKNOWN")
        style_info = NODE_STYLES.get(node_type, NODE_STYLES.get("THOUGHT"))

        style = {
            "background-color": style_info["bg"],
            "border": style_info["border"],
            "color": "#333",
            "width": "200px",
            "font-size": "0.75rem",
        }

        # Smaller nodes for internal agent steps
        if node_type in ("TOOL_CALL", "TOOL_OUTPUT", "REASONING"):
            style["width"] = "180px"
            style["font-size"] = "0.7rem"

        # Content
        content_preview = s.get("content", "")
        if len(content_preview) > 60:
            content_preview = content_preview[:60] + "..."

        agent_name = s.get("agent", "System")
        type_label = style_info["label"]
        label = f"**{agent_name}**\n\n_{type_label}_\n\n{content_preview}"

        nodes.append(StreamlitFlowNode(
            id=s["id"],
            pos=(0, 0),
            data={"content": label},
            node_type="default",
            style=style,
            draggable=True,
        ))

        # --- Edge Creation ---
        metadata = s.get("metadata", {})

        if node_type == "DEBATE" and "parent" in metadata:
            # Fan-out edge: FANOUT -> DEBATE
            edges.append(StreamlitFlowEdge(
                id=f"e-{metadata['parent']}-{s['id']}",
                source=metadata["parent"],
                target=s["id"],
                animated=True,
                style={"stroke": EDGE_COLORS["fanout"]},
            ))
        elif node_type in ("TOOL_CALL", "TOOL_OUTPUT", "REASONING") and "parent" in metadata:
            # Internal agent step: parent -> this step (chain within agent)
            edges.append(StreamlitFlowEdge(
                id=f"e-{metadata['parent']}-{s['id']}",
                source=metadata["parent"],
                target=s["id"],
                animated=False,
                style={"stroke": EDGE_COLORS["internal"], "stroke-dasharray": "5,5"},
            ))
        elif node_type == "COLLECT" and "sources" in metadata:
            # Collect edges: last step of each agent -> COLLECT
            for src_id in metadata["sources"]:
                edges.append(StreamlitFlowEdge(
                    id=f"e-{src_id}-{s['id']}",
                    source=src_id,
                    target=s["id"],
                    animated=True,
                    style={"stroke": EDGE_COLORS["collect"]},
                ))
        elif node_type == "SYNTHESIS" and "parent" in metadata:
            # COLLECT -> SYNTHESIS
            edges.append(StreamlitFlowEdge(
                id=f"e-{metadata['parent']}-{s['id']}",
                source=metadata["parent"],
                target=s["id"],
                animated=True,
                style={"stroke": EDGE_COLORS["synthesis"]},
            ))
        elif node_type not in ("FANOUT", "DEBATE", "COLLECT", "SYNTHESIS",
                               "TOOL_CALL", "TOOL_OUTPUT", "REASONING"):
            # Legacy linear chain
            if i < len(steps) - 1:
                edges.append(StreamlitFlowEdge(
                    id=f"e-{s['id']}-{steps[i+1]['id']}",
                    source=s["id"],
                    target=steps[i+1]["id"],
                    animated=True,
                    style={"stroke": EDGE_COLORS["linear"]},
                ))

    return StreamlitFlowState(nodes=nodes, edges=edges)


def build_step_index(steps):
    """Build a dict of step_id -> step for quick detail lookup."""
    return {s["id"]: s for s in steps}


def render_step_detail(step):
    """Render a detail panel for a selected trace step."""
    if not step:
        return

    node_type = step.get("type", "UNKNOWN")
    agent = step.get("agent", "System")
    metadata = step.get("metadata", {})
    full_content = metadata.get("full_content", step.get("content", ""))
    tool_names = metadata.get("tool_names", [])
    db_name = metadata.get("db", "")

    style_info = NODE_STYLES.get(node_type, {"label": node_type})

    st.markdown(f"#### {agent} - {style_info['label']}")

    cols = st.columns(3)
    with cols[0]:
        st.caption(f"Type: `{node_type}`")
    with cols[1]:
        if db_name:
            st.caption(f"Database: `{db_name}`")
    with cols[2]:
        if tool_names:
            st.caption(f"Tools: `{', '.join(tool_names)}`")

    st.markdown(f'<div class="trace-detail-box">{full_content}</div>', unsafe_allow_html=True)


# --- Main Application ---
st.title("Seocho Agent Studio")

# Initialize Session State
if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "current_trace_steps" not in st.session_state:
    st.session_state["current_trace_steps"] = []
if "trace_version" not in st.session_state:
    st.session_state["trace_version"] = 0
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())
if "query_mode" not in st.session_state:
    st.session_state["query_mode"] = "router"
if "selected_node" not in st.session_state:
    st.session_state["selected_node"] = None
if "workspace_id" not in st.session_state:
    st.session_state["workspace_id"] = "default"
if "semantic_databases" not in st.session_state:
    st.session_state["semantic_databases"] = "kgnormal,kgfibo"

# Layout
col1, col2 = st.columns([1, 1], gap="large")

with col1:
    st.subheader("Conversation")

    mode_map = {
        "Router": "router",
        "Debate": "debate",
        "Semantic": "semantic",
    }
    current_mode_label = next(
        (label for label, value in mode_map.items() if value == st.session_state["query_mode"]),
        "Router",
    )
    selected_mode_label = st.radio(
        "Execution Mode",
        options=["Router", "Debate", "Semantic"],
        index=["Router", "Debate", "Semantic"].index(current_mode_label),
        horizontal=True,
        help="Router: default route, Debate: all DB agents in parallel, Semantic: entity resolution + LPG/RDF agents.",
    )
    st.session_state["query_mode"] = mode_map[selected_mode_label]

    with st.expander("Runtime Options", expanded=False):
        st.session_state["workspace_id"] = st.text_input(
            "workspace_id",
            value=st.session_state["workspace_id"],
            help="Single-tenant MVP default is 'default'.",
        )
        st.session_state["semantic_databases"] = st.text_input(
            "Semantic Databases (comma-separated)",
            value=st.session_state["semantic_databases"],
            help="Only used in Semantic mode.",
        )

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
                    payload = {
                        "query": prompt,
                        "user_id": st.session_state["session_id"],
                        "workspace_id": st.session_state["workspace_id"],
                    }

                    if st.session_state["query_mode"] == "debate":
                        endpoint = f"{API_URL}/run_debate"
                    elif st.session_state["query_mode"] == "semantic":
                        endpoint = f"{API_URL}/run_agent_semantic"
                        dbs = [
                            token.strip()
                            for token in st.session_state["semantic_databases"].split(",")
                            if token.strip()
                        ]
                        if dbs:
                            payload["databases"] = dbs
                    else:
                        endpoint = f"{API_URL}/run_agent"

                    resp = requests.post(endpoint, json=payload)

                    if resp.status_code == 200:
                        data = resp.json()
                        response_text = data.get("response", "")
                        trace_steps = data.get("trace_steps", [])

                        message_placeholder.markdown(response_text)

                        st.session_state["messages"].append({"role": "assistant", "content": response_text})

                        # Update Trace View
                        st.session_state["current_trace_steps"] = trace_steps
                        st.session_state["trace_version"] += 1
                        st.session_state["selected_node"] = None
                    else:
                        st.error(f"API Error: {resp.status_code} - {resp.text}")

                except Exception as e:
                    st.error(f"Connection Error: {e}")

with col2:
    st.subheader("Live Agent Flow")
    if st.session_state["query_mode"] == "debate":
        st.caption("Parallel Debate: fan-out / internal reasoning / collect / synthesize")
    elif st.session_state["query_mode"] == "semantic":
        st.caption("Semantic mode: entity extract/dedup/fulltext -> router -> LPG/RDF -> answer generation")
    else:
        st.caption("Automatic visualization of agent execution.")

    if st.session_state["current_trace_steps"]:
        # Build flow state
        flow_state = get_trace_flow(st.session_state["current_trace_steps"])

        # Render flow graph (returns selected node id on click)
        selected = streamlit_flow(
            "trace_flow",
            flow_state,
            layout=TreeLayout(direction='down'),
            fit_view=True,
            height=450,
            enable_pane_menu=True,
            enable_node_menu=True,
            get_node_on_click=True,
            key=f"flow_{st.session_state['trace_version']}",
        )

        # Track selected node
        if selected and selected != st.session_state.get("selected_node"):
            st.session_state["selected_node"] = selected

        # --- Step Detail Panel ---
        st.markdown("---")
        st.subheader("Step Detail")

        step_index = build_step_index(st.session_state["current_trace_steps"])
        selected_id = st.session_state.get("selected_node")

        if selected_id and selected_id in step_index:
            render_step_detail(step_index[selected_id])
        else:
            st.info("Click a node in the flow graph to see its full content, tool calls, and reasoning.")
    else:
        st.info("Agent steps will appear here automatically after you send a message.")
