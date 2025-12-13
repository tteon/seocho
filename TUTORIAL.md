# Tutorial: Ingesting Custom Data

This guide explains how to load your own datasets from HuggingFace into the GraphRAG system.

## Prerequisites
- A HuggingFace account.
- A dataset uploaded to HuggingFace (or use a public one).
- Your `HF_TOKEN` set in the `.env` file (run `./setup_env.sh`).

## Step 1: Configure the Collector
Open `extraction/collector.py` and modify the `dataset_url` in the `DataCollector` class.

```python
class DataCollector:
    def __init__(self, use_mock: bool = False):
        self.use_mock = use_mock
        # CHANGE THIS to your dataset ID
        self.dataset_url = "your-username/your-dataset-name" 
        self.target_categories = ['Your', 'Target', 'Categories']
```

## Step 2: Schema Matching
Ensure your dataset has the following columns (or modify `collector.py` to map them):
- `category`: Used for filtering (optional).
- `references` (or `text`, `content`): The actual text content to process.

The `DataCollector` attempts to auto-detect content columns, but strictly speaking, it looks for `references` or `text`.

## Step 3: Run Extraction
Restart the extraction service to pick up the changes:

```bash
docker-compose restart extraction-service
```

The service will automatically pull your dataset, process the text, extract entities, and load them into Neo4j!

## Step 4: Verify
1.  Check **Neo4j** ([http://localhost:7474](http://localhost:7474)) to visualize your new graph.
