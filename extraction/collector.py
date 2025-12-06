import time
import random
from typing import List, Dict

class DataCollector:
    def __init__(self):
        pass

    def collect_raw_data(self) -> List[Dict]:
        """
        Simulates collecting raw data from a source.
        In a real app, this might fetch from an API, read files, etc.
        """
        print("Collecting raw data...")
        # Simulate latency
        time.sleep(1)
        
        # Generate dummy data
        data = [
            {"id": "doc_1", "content": "GraphRAG combines knowledge graphs with RAG.", "source": "internal_wiki"},
            {"id": "doc_2", "content": "Neo4j is a popular graph database.", "source": "tech_blog"},
            {"id": "doc_3", "content": "DataHub enables data discovery and observability.", "source": "documentation"},
            {"id": "doc_4", "content": "LLM agents can reason over graph structures.", "source": "research_paper"}
        ]
        return data
