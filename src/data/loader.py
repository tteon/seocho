"""
Data Loader
Loads data from external sources into Opik datasets.
Refactored from rawdataload.py
"""
import os
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from huggingface_hub import login
from datasets import load_dataset
from opik import Opik

from src.config.settings import OPIK_URL, OPIK_WORKSPACE, OPIK_PROJECT

# Set environment
load_dotenv()
os.environ["OPIK_URL_OVERRIDE"] = OPIK_URL
os.environ["OPIK_WORKSPACE"] = OPIK_WORKSPACE
os.environ["OPIK_PROJECT_NAME"] = OPIK_PROJECT


def sanitize_value(v):
    """Convert NumPy types to Python native types for JSON compatibility."""
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.int64, np.int32, np.float64, np.float32)):
        return v.item()
    if isinstance(v, list):
        return [sanitize_value(i) for i in v]
    if isinstance(v, dict):
        return {k: sanitize_value(val) for k, val in v.items()}
    return v


def load_fibo_dataset(
    hf_dataset: str = "Linq-AI-Research/FinDER",
    opik_dataset_name: str = "fibo-evaluation-dataset",
    batch_size: int = 1000
) -> bool:
    """
    Load FIBO evaluation data from HuggingFace to Opik.
    
    Args:
        hf_dataset: HuggingFace dataset name
        opik_dataset_name: Target Opik dataset name
        batch_size: Batch size for upload
    
    Returns:
        Success status
    """
    print(">>> Authenticating with HuggingFace...")
    hf_token = os.getenv("HUGGINGFACE_TOKEN")
    if hf_token:
        login(token=hf_token)
    
    print(f">>> Loading dataset: {hf_dataset}")
    dataset = load_dataset(hf_dataset, split="train")
    df = dataset.to_pandas()
    print(f">>> Loaded {len(df)} rows")
    
    # Initialize Opik
    client = Opik()
    opik_dataset = client.get_or_create_dataset(name=opik_dataset_name)
    
    # Transform data
    print(">>> Transforming data...")
    dataset_items = []
    for _, row in df.iterrows():
        dataset_items.append({
            "input": {"text": str(row.get("text", ""))},
            "expected_output": str(row.get("answer", "")),
            "metadata": {
                "id": sanitize_value(row.get("_id")),
                "category": sanitize_value(row.get("category")),
                "reasoning": sanitize_value(row.get("reasoning")),
                "type": sanitize_value(row.get("type")),
                "references": sanitize_value(row.get("references"))
            }
        })
    
    # Upload in batches
    print(f">>> Uploading to Opik dataset: {opik_dataset_name}")
    for i in range(0, len(dataset_items), batch_size):
        batch = dataset_items[i:i + batch_size]
        opik_dataset.insert(items=batch)
        print(f"    Progress: {i + len(batch)} / {len(dataset_items)}")
    
    print(">>> Data load complete!")
    return True


if __name__ == "__main__":
    load_fibo_dataset()
