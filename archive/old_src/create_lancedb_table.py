#!/usr/bin/env python3
"""
Create LanceDB table from local data
This script creates the fibo_context table without needing Opik connection.
"""
import os
import sys
import json
import lancedb
import pyarrow as pa
from openai import OpenAI
from tqdm import tqdm

# Configuration
DB_PATH = os.getenv("LANCEDB_PATH", "/workspace/data/lancedb")
TABLE_NAME = "fibo_context"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

# Sample data file paths to try
DATA_FILES = [
    "/workspace/output/opik_exports/fibo-evaluation-dataset_export.json",
    "/workspace/kgbuild-traces.json",
    "/workspace/output/opik_exports/mini-test-dataset_export.json"
]

def get_embedding(text: str, client) -> list:
    """Generate embedding for text"""
    text = text.replace("\n", " ")
    if not text.strip():
        return [0.0] * EMBEDDING_DIM
    try:
        return client.embeddings.create(input=[text], model=EMBEDDING_MODEL).data[0].embedding
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return [0.0] * EMBEDDING_DIM

def load_from_json_file(filepath):
    """Load data from JSON export file"""
    print(f"📂 Loading data from: {filepath}")
    with open(filepath, 'r') as f:
        data = json.load(f)
    
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Could be in various formats
        if 'items' in data:
            items = data['items']
        elif 'dataset_items' in data:
            items = data['dataset_items']
        else:
            print(f"Unexpected JSON structure. Keys: {data.keys()}")
            return []
    
    print(f"✅ Loaded {len(items)} items")
    return items

def build_table():
    """Create LanceDB table"""
    print("=" * 70)
    print("🔨 Building LanceDB Table from Local Data")
    print("=" * 70)
    
    # Initialize OpenAI client
    openai_client = OpenAI()
    
    # Create DB directory
    os.makedirs(DB_PATH, exist_ok=True)
    db = lancedb.connect(DB_PATH)
    
    # Find available data file
    items = []
    for filepath in DATA_FILES:
        if os.path.exists(filepath):
            try:
                items = load_from_json_file(filepath)
                if items:
                    break
            except Exception as e:
                print(f"⚠️  Error loading {filepath}: {e}")
                continue
    
    if not items:
        print("⚠️  No data files found. Creating table with sample data...")
        items = [
            {
                "id": "sample_1",
                "input": {"text": "What is a credit derivative?"},
                "expected_output": "A credit derivative is a financial instrument.",
                "metadata": {"references": ["FIBO definition of CreditDerivative"]}
            }
        ]
    
    # Process items
    print(f"\n📊 Processing {len(items)} items...")
    data_to_insert = []
    seen_ids = set()
    
    for idx, item in enumerate(tqdm(items, desc="Processing")):
        # Extract text content
        text_content = ""
        
        # Try to get references from metadata
        metadata = item.get("metadata", {})
        refs = metadata.get("references", "")
        if refs:
            text_content = " ".join([str(r) for r in refs]) if isinstance(refs, list) else str(refs)
        
        # Fallback: use input/output
        if not text_content:
            input_data = item.get("input", {})
            if isinstance(input_data, dict):
                text_content = input_data.get("text", str(input_data))
            else:
                text_content = str(input_data)
            
            # Add expected output too
            expected = item.get("expected_output", "")
            if expected:
                text_content += " " + str(expected)
        
        if not text_content or len(text_content.strip()) < 10:
            continue
        
        # Generate ID
        item_id = item.get("id", f"item_{idx}")
        if item_id in seen_ids:
            item_id = f"{item_id}_{idx}"
        seen_ids.add(str(item_id))
        
        # Generate embedding
        vector = get_embedding(text_content, openai_client)
        
        data_to_insert.append({
            "id": str(item_id),
            "vector": vector,
            "text": text_content[:5000],  # Limit text length
            "source": "local_export"
        })
    
    if not data_to_insert:
        print("❌ No valid data to insert!")
        return False
    
    print(f"\n✅ Prepared {len(data_to_insert)} records")
    
    # Create schema
    schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("vector", pa.fixed_size_list(pa.float32(), EMBEDDING_DIM)),
        pa.field("text", pa.string()),
        pa.field("source", pa.string())
    ])
    
    # Create table
    print(f"\n⚙️  Creating table '{TABLE_NAME}'...")
    tbl = db.create_table(TABLE_NAME, data=data_to_insert, schema=schema, mode="overwrite")
    print(f"✅ Table created with {len(data_to_insert)} rows")
    
    # Create indexes if enough data
    if len(data_to_insert) > 100:
        print("\n⚙️  Building Vector Index (IVF-PQ)...")
        try:
            tbl.create_index(
                metric="cosine",
                vector_column_name="vector",
                num_partitions=min(16, len(data_to_insert) // 10),
                num_sub_vectors=96
            )
            print("✅ Vector index created")
        except Exception as e:
            print(f"⚠️  Vector index creation failed: {e}")
    
    # Create FTS index
    print("\n⚙️  Building Full-Text Search Index...")
    try:
        tbl.create_fts_index("text", replace=True)
        print("✅ FTS index created")
    except ImportError:
        print("⚠️  'tantivy' not installed. FTS index skipped.")
    except Exception as e:
        print(f"⚠️  FTS index creation failed: {e}")
    
    print("\n" + "=" * 70)
    print("✅ LanceDB Table Creation Complete!")
    print("=" * 70)
    print(f"📍 Location: {DB_PATH}/{TABLE_NAME}")
    print(f"📊 Records: {len(data_to_insert)}")
    
    return True

if __name__ == "__main__":
    success = build_table()
    sys.exit(0 if success else 1)
