import streamlit as st
import requests
import os

# Configuration
SEMANTIC_SERVICE_URL = os.getenv("SEMANTIC_SERVICE_URL", "http://semantic-service:8000")

st.set_page_config(page_title="GraphRAG Chatbot", page_icon="🤖")

st.title("GraphRAG Chatbot 🤖")
st.markdown("Ask questions about your knowledge graph!")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat messages from history on app rerun
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# React to user input
if prompt := st.chat_input("What is GraphRAG?"):
    # Display user message in chat message container
    st.chat_message("user").markdown(prompt)
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})

    # Call Semantic Layer (Mocked for now as we don't have a chat endpoint yet)
    # In a real implementation, we would call an endpoint like /chat
    # response = requests.post(f"{SEMANTIC_SERVICE_URL}/chat", json={"query": prompt})
    # bot_response = response.json()["answer"]
    
    # Simulating a response based on the graph context
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        full_response = f"I received your query: '{prompt}'. \n\nThis is a mocked response from the GraphRAG chatbot. In a full implementation, I would query the Neo4j graph via the Semantic Layer to provide a grounded answer."
        message_placeholder.markdown(full_response)
    
    # Add assistant response to chat history
    st.session_state.messages.append({"role": "assistant", "content": full_response})
