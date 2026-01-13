"""
LanceDB Vector Indexer
Indexes text data with embeddings for hybrid search (vector + full-text).
Merged from hybridagent_indexing.py and create_lancedb_table.py
"""
import os
import json
import lancedb
import pyarrow as pa
from openai import OpenAI
from opik import Opik
from tqdm import tqdm
from typing import List, Dict, Any, Optional

from src.config.settings import (
    LANCEDB_PATH, LANCEDB_TABLE, 
    EMBEDDING_MODEL, EMBEDDING_DIM
)


class LanceDBIndexer:
    """
    Indexes text data into LanceDB with embeddings.
    Supports multiple data sources: Opik API or local JSON files.
    """
    
    def __init__(self, db_path: str = None, table_name: str = None):
        self.db_path = db_path or LANCEDB_PATH
        self.table_name = table_name or LANCEDB_TABLE
        self.openai_client = OpenAI()
        self.db = None
        
    def connect(self):
        """Connect to LanceDB, creating directory if needed."""
        os.makedirs(self.db_path, exist_ok=True)
        self.db = lancedb.connect(self.db_path)
        return self
    
    def get_embedding(self, text: str) -> List[float]:
        """Generate embedding for text."""
        text = text.replace("\n", " ")
        if not text.strip():
            return [0.0] * EMBEDDING_DIM
        try:
            return self.openai_client.embeddings.create(
                input=[text], 
                model=EMBEDDING_MODEL
            ).data[0].embedding
        except Exception as e:
            print(f"Error generating embedding: {e}")
            return [0.0] * EMBEDDING_DIM
    
    def build_from_opik(self, dataset_name: str = "fibo-evaluation-dataset"):
        """
        Build index from Opik dataset (production mode).
        
        Args:
            dataset_name: Name of the Opik dataset to index
        """
        if self.db is None:
            self.connect()
            
        print(f"🔍 Fetching dataset '{dataset_name}' from Opik...")
        try:
            opik_client = Opik()
            dataset = opik_client.get_dataset(name=dataset_name)
            items = dataset.get_items()
        except Exception as e:
            print(f"Dataset Load Error: {e}")
            return False
        
        print(f"✅ Found {len(items)} items. Starting ingestion...")
        return self._index_items(items, source="opik_dataset")
    
    def build_from_json(self, json_paths: List[str]):
        """
        Build index from local JSON files (fallback mode).
        
        Args:
            json_paths: List of JSON file paths to try
        """
        if self.db is None:
            self.connect()
            
        items = []
        for filepath in json_paths:
            if os.path.exists(filepath):
                try:
                    print(f"📂 Loading data from: {filepath}")
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                    
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        items = data.get('items', data.get('dataset_items', []))
                    
                    if items:
                        break
                except Exception as e:
                    print(f"⚠️ Error loading {filepath}: {e}")
                    continue
        
        if not items:
            print("❌ No data files found!")
            return False
            
        print(f"✅ Loaded {len(items)} items")
        return self._index_items(items, source="local_json")
    
    def _index_items(self, items: List[Dict[str, Any]], source: str) -> bool:
        """Internal method to index items."""
        data_to_insert = []
        seen_ids = set()
        
        for idx, item in enumerate(tqdm(items, desc="Processing Data")):
            # Extract text content
            text_content = self._extract_text(item)
            if not text_content or len(text_content.strip()) < 10:
                continue
            
            # Generate ID
            item_id = self._get_item_id(item, idx)
            if item_id in seen_ids:
                item_id = f"{item_id}_{idx}"
            seen_ids.add(str(item_id))
            
            # Generate embedding
            vector = self.get_embedding(text_content)
            
            data_to_insert.append({
                "id": str(item_id),
                "vector": vector,
                "text": text_content[:5000],
                "source": source
            })
        
        if not data_to_insert:
            print("❌ No valid data to insert!")
            return False
        
        print(f"✅ Prepared {len(data_to_insert)} records")
        
        # Create schema and table
        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("vector", pa.fixed_size_list(pa.float32(), EMBEDDING_DIM)),
            pa.field("text", pa.string()),
            pa.field("source", pa.string())
        ])
        
        tbl = self.db.create_table(
            self.table_name, 
            data=data_to_insert, 
            schema=schema, 
            mode="overwrite"
        )
        print(f"✅ Table '{self.table_name}' created with {len(data_to_insert)} rows")
        
        # Create indexes
        self._create_indexes(tbl, len(data_to_insert))
        return True
    
    def _extract_text(self, item: Dict) -> str:
        """Extract text content from an item."""
        # Try references from metadata
        metadata = item.get("metadata", {})
        refs = metadata.get("references", "")
        if refs:
            return " ".join([str(r) for r in refs]) if isinstance(refs, list) else str(refs)
        
        # Fallback to input/output
        input_data = item.get("input", {})
        if isinstance(input_data, dict):
            text = input_data.get("text", str(input_data))
        else:
            text = str(input_data)
        
        expected = item.get("expected_output", "")
        if expected:
            text += " " + str(expected)
        
        return text
    
    def _get_item_id(self, item: Dict, idx: int) -> str:
        """Get ID for an item."""
        metadata = item.get("metadata", {})
        if metadata.get("id"):
            return str(metadata["id"])
        return item.get("id", f"item_{idx}")
    
    def _create_indexes(self, tbl, row_count: int):
        """Create vector and full-text indexes."""
        # Vector index (IVF-PQ) for larger datasets
        if row_count > 100:
            print("⚙️ Building Vector Index (IVF-PQ)...")
            try:
                tbl.create_index(
                    metric="cosine",
                    vector_column_name="vector",
                    num_partitions=min(16, row_count // 10),
                    num_sub_vectors=96
                )
                print("✅ Vector index created")
            except Exception as e:
                print(f"⚠️ Vector index creation failed: {e}")
        
        # Full-text search index
        print("⚙️ Building Full-Text Search Index...")
        try:
            tbl.create_fts_index("text", replace=True)
            print("✅ FTS index created")
        except ImportError:
            print("⚠️ 'tantivy' not installed. FTS index skipped.")
        except Exception as e:
            print(f"⚠️ FTS index creation failed: {e}")
        
        print(f"✅ Indexing complete: {self.db_path}/{self.table_name}")


def build_lancedb_index():
    """CLI entry point for LanceDB indexing."""
    indexer = LanceDBIndexer()
    indexer.connect()
    
    # Try Opik first, fallback to local files
    try:
        success = indexer.build_from_opik()
    except Exception:
        print("⚠️ Opik unavailable, trying local files...")
        json_paths = [
            "/workspace/output/opik_exports/fibo-evaluation-dataset_export.json",
            "/workspace/kgbuild-traces.json",
        ]
        success = indexer.build_from_json(json_paths)
    
    return success


if __name__ == "__main__":
    build_lancedb_index()
