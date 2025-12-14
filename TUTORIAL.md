# Seocho Agent Studio Tutorial

This guide covers the core workflows of the Seocho Agent Studio.

## 1. Entity Extraction & Linking (Custom Dataset)

You can process your own text files to populate the Knowledge Graph.

### **Step 1: Prepare Data**
Place your text files (e.g., `financial_report.txt`) in `data/inputs/`.

### **Step 2: Run Extraction Pipeline**
The system uses the `extraction/pipeline.py` script.
```bash
# Inside the extraction-service container or locally
python extraction/main.py --mode=extraction --input=data/inputs/
```
*What happens:*
1. **Extraction**: LLM identifies entities (e.g., "Apple Inc.", "Tim Cook").
2. **Linking**: Maps aliases to canonical IDs (e.g., "Apple" -> `Apple Inc. (ORG-001)`).
3. **Ingestion**: Creates Nodes and Relationships in Neo4j.

---

## 2. Designing the Agent Flow

We use a **Router -> Graph -> DBA -> Supervisor** pattern.

### **The Architecture**
- **Router**: "I see a multi-hop question. Send to Graph Agent."
- **Graph Agent**: "I need to find connections between A and B. DB, please check the schema."
- **Graph DBA**: "I found the schema. Executing Cypher query... Here are the results."
- **Graph Agent**: "Looks good. Supervisor, here is the answer."
- **Supervisor**: "User, here is your final answer..."

### **Customizing the DBA**
Edit `extraction/agent_server.py` to add new databases or few-shot examples.

```python
agent_graph_dba = Agent(
    name="GraphDBA",
    instructions="""
    ...
    ## Case: My Custom Dataset
    User: "Who owns X?"
    Cypher: "MATCH (a)-[:OWNS]->(b) ..."
    """,
    tools=[get_databases_tool, get_schema_tool, execute_cypher_tool]
)
```

---

## 3. Visualization & Debugging

### **Using Streamlit Flow**
1. Go to [http://localhost:8501](http://localhost:8501).
2. Chat with the agent.
3. Observe the generated graph on the right.
    - **Blue Node**: Your Input.
    - **Orange Node**: Agent Thought / Tool Call.
    - **Green Node**: Final Response.
    - **Purple Node**: Tool Result.

### **Debugging Tips**
- **Cycle Detected?** If you see Graph Agent and DBA bouncing back and forth, check the DBA instructions. It might be failing to generate valid Cypher.
- **Wrong Router Decision?** Check the Router instructions in `agent_server.py` and refine the "Trigger When" descriptions.

---

## 4. OpenAI Tracing

The system is instrumented with `openai-agents` native tracing.

### **Enabling Tracing**
Ensure your `OPENAI_API_KEY` is set. The agent server wraps execution in a trace context:

```python
with trace(f"Request {user_id}"):
    await Runner.run(...)
```

### **Viewing Traces**
Logs are sent to your configured OpenAI Trace destination (if configured) or printed to stdout in the container logs.
```bash
docker logs extraction-service
```
Future updates will support direct export to the OpenAI Dashboard.

---

## 5. Testing & Reproducibility

To ensure your agent logic is robust, you can run the included test suite.

### **Running Tests**
Tests are located in `extraction/tests/`.

```bash
docker-compose exec extraction-service pytest tests/
```

### **What is tested?**
- **Tools**: Unit tests for `get_schema_tool`, `get_databases_tool`.
- **API**: Integration validity of the `agent_server` endpoints.
