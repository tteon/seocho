# Tutorial: Zero to GraphRAG

This comprehensive guide takes you through the entire workflow: bringing your own data, customizing agents, and visualizing the results.

## 1. Bring Your Own Data (Data Ingestion)
The system supports auto-syncing schema from your data.

### Step 1.1: Configure Source
Open `extraction/collector.py`. Set your HuggingFace dataset ID:
```python
self.dataset_url = "Linq-AI-Research/FinDER"  # Your Dataset ID
```
Ensure your dataset has a text column (e.g., `text`, `content`, `references`).

### Step 1.2: Run Extraction & Auto-Sync
Run the extraction pipeline. The system will **automatically discover** new entity types from your data and update the schema.
```bash
docker-compose run extraction-service
```
- **Check**: Look at `extraction/conf/schemas/baseline.yaml`. You'll see new definitions (e.g., `Company`, `Person`) added automatically!

---

## 2. Customize Your Agents
Define the "Brain" of your system. We use a centralized agent registry.

### Step 2.1: Edit Agent Definitions
Open `extraction/agents.py`. You can define new agents here.
```python
# Example: Adding a new expert
billing_agent = Agent(
    name="BillingExpert",
    instructions="You are an expert in financial billing and invoicing."
)

# Add to Manager's handoffs
manager_agent = Agent(
    name="Manager",
    instructions="...",
    handoffs=[research_agent, billing_agent] # <--- Add here
)
```
### step 2.2: Restart Interface
To apply changes to the Evaluation Interface:
```bash
docker-compose restart evaluation-interface
```

---

## 3. Evaluation & Visualization
Monitor your agents' reasoning and performance.

### Step 3.1: Chat & Analyze
Access the **Evaluation Interface** at **[http://localhost:8501](http://localhost:8501)**.
- Chat with your manager agent.
- The system uses **AdvancedSQLiteSession** to track precise token usage and history.

### Step 3.2: The NeoDash Experience
Access **NeoDash** at **[http://localhost:5005](http://localhost:5005)**.

1.  **Connect**: URI `bolt://neo4j:7687`, User `neo4j`, Pass `password`.
2.  **Load Dashboard**:
    - Click **New Dashboard**.
    - Click the **Load** icon (folder/code symbol).
    - Paste the content of **`neodash_dashboard.json`** (found in the project root).
3.  **Explore**:
    - **Overview**: See real-time Agent Popularity and Token Costs.
    - **Trace Inspector**: Visualize the graph of `(:Trace)-[:NEXT]->(:Step)`. See exactly how agents handed off tasks!
