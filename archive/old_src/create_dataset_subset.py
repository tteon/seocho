import os
from opik import Opik

def create_subset(source_dataset_name: str, target_dataset_name: str, sample_size: int = 50):
    client = Opik()
    
    print(f"🔍 Fetching dataset '{source_dataset_name}'...")
    try:
        source_dataset = client.get_dataset(name=source_dataset_name)
        items = source_dataset.get_items()
    except Exception as e:
        print(f"❌ Error: {e}")
        return

    print(f"✅ Found {len(items)} items. Creating subset of size {sample_size}...")
    
    subset_items = items[:sample_size]
    
    # Create or get target dataset
    target_dataset = client.get_or_create_dataset(name=target_dataset_name)
    
    # Clear target dataset if it exists (optional)
    # target_dataset.clear() 
    
    target_dataset.insert(subset_items)
    print(f"🚀 Subset '{target_dataset_name}' created with {len(subset_items)} items.")

if __name__ == "__main__":
    # Configure Opik
    os.environ["OPIK_URL_OVERRIDE"] = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
    os.environ["OPIK_PROJECT_NAME"] = "graph-agent-ablation"
    
    create_subset(
        source_dataset_name="fibo-evaluation-dataset",
        target_dataset_name="fibo-subset-50",
        sample_size=50
    )
