import time
import pandas as pd
from typing import List, Dict

class DataCollector:
    def __init__(self, use_mock: bool = False):
        self.use_mock = use_mock
        # In a real scenario, could accept dataset URL as config
        self.dataset_url = "hf://datasets/Linq-AI-Research/FinDER/data/train-00000-of-00001.parquet"
        self.target_categories = ['Financials', 'Company overview', 'Legal']

    def collect_raw_data(self) -> List[Dict]:
        """
        Collects data from HuggingFace dataset or returns mock data.
        Returns a list of dicts with standard keys: id, content, category, source.
        """
        df = None
        
        if self.use_mock:
            print("Generating mock data (simulating FinDER dataset structure)...")
            mock_data = [
                {
                    "_id": "mock_1",
                    "text": "SpaceX is a private aerospace manufacturer.",
                    "category": "Company overview",
                    "references": [
                        "SpaceX was founded by Elon Musk in 2002.",
                        "It designs, manufactures and launches advanced rockets and spacecraft."
                    ]
                },
                {
                     "_id": "mock_2", 
                     "text": "Tesla is an automotive and clean energy company.",
                     "category": "Company overview",
                     "references": [
                         "Tesla, Inc. is an American electric vehicle and clean energy company based in Austin, Texas."
                     ]
                },
                {
                    "_id": "mock_3",
                    "text": "Apple Inc. results for fiscal year.",
                    "category": "Financials",
                    "references": [
                        "Apple announced financial results for its fiscal 2023 fourth quarter ended September 30, 2023."
                    ] 
                }
            ]
            df = pd.DataFrame(mock_data)

        else:
            print(f"Loading dataset from {self.dataset_url}...")
            try:
                # Use HF datasets library
                from datasets import load_dataset
                
                # Parse dataset name from URL or config
                # Example URL: hf://datasets/Linq-AI-Research/FinDER/data/train-00000-of-00001.parquet
                # We'll assume the user wants 'Linq-AI-Research/FinDER' or similar. 
                # For this implementation, we will try to load it cleanly.
                # If self.dataset_url is a direct parquet link, we can still use pandas or datasets.
                # But user asked for "huggingface data load" which implies using the `datasets` lib.
                
                # Check if it looks like a dataset name or a file path
                if "hf://datasets/" in self.dataset_url:
                     # Fallback to pandas for direct parquet files as it's often more robust for specific file URLs
                     # But let's try to support standard dataset names too if provided.
                     df = pd.read_parquet(self.dataset_url)
                else:
                    # Assume it's a dataset name like 'Linq-AI-Research/FinDER'
                    ds = load_dataset(self.dataset_url, split="train")
                    df = ds.to_pandas()

            except Exception as e:
                print(f"Error loading dataset: {e}")
                return []

        # Common Processing Logic
        try:
            # Helper to normalize category checks
            # Handle standard HF dataset structures or the specific parquet schema
            
            # If columns missing, try to adapt
            if 'category' not in df.columns:
                print("Refining schema: 'category' column missing, defaulting to 'general'.")
                df['category'] = 'general'
            
            if 'references' not in df.columns:
                # Try to find a content column
                potential_cols = ['text', 'content', 'document']
                for c in potential_cols:
                    if c in df.columns:
                        df['references'] = df[c]
                        break
            
            if 'references' not in df.columns:
                 print(f"Error: Could not find content column. Available: {df.columns}")
                 return []

            # Filter categories if present in data
            if 'category' in df.columns and self.target_categories:
                # Only filter if the dataset actually supports these categories
                # If we defaulted to 'general', we shouldn't filter by target_categories unless 'general' is target.
                # For safety, strict filtering only if values match specific taxonomy.
                 filtered_df = df[df['category'].isin(self.target_categories)]
                 if len(filtered_df) == 0:
                     print("Warning: Filtering resulted in 0 rows. Using all data instead.")
                     filtered_df = df
            else:
                 filtered_df = df
            
            print(f"Processing {len(filtered_df)} rows.")
            
            data = []
            for idx, row in filtered_df.iterrows():
                # Map dataset columns to internal schema
                doc_id = row.get('_id', row.get('id', f"doc_{idx}"))
                
                # 'references' is the source of extracted content per user plan
                content_raw = row['references']
                if isinstance(content_raw, list) or hasattr(content_raw, 'tolist'):
                    content = "\n".join([str(r) for r in content_raw])
                else:
                    content = str(content_raw)

                item = {
                    "id": str(doc_id)[:50],
                    "content": content,
                    "category": row.get('category', 'general'),
                    # Optional: preserve original text if needed for other uses
                    # "full_text": row.get('text', ''),
                    "source": "FinDER_HF" if not self.use_mock else "FinDER_Mock"
                }
                data.append(item)
                
            return data

        except Exception as e:
            print(f"Error processing data: {e}")
            return []
