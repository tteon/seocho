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
                df = pd.read_parquet(self.dataset_url)
            except Exception as e:
                print(f"Error loading real dataset: {e}")
                return []

        # Common Processing Logic
        try:
            # Helper to normalize category checks
            if 'category' not in df.columns or 'references' not in df.columns:
                 print("Warning: Expected columns 'category' or 'references' not found. Available:", df.columns)

            # Filter categories
            filtered_df = df[df['category'].isin(self.target_categories)]
            
            print(f"Filtered {len(filtered_df)} rows from {len(df)} total rows.")
            
            data = []
            for idx, row in filtered_df.iterrows():
                # Map dataset columns to internal schema
                doc_id = row.get('_id', f"doc_{idx}")
                
                # 'references' is the source of extracted content per user plan
                content_raw = row['references']
                if isinstance(content_raw, list) or hasattr(content_raw, 'tolist'):
                    content = "\n".join([str(r) for r in content_raw])
                else:
                    content = str(content_raw)

                item = {
                    "id": str(doc_id)[:50],
                    "content": content,
                    "category": row['category'],
                    # Optional: preserve original text if needed for other uses
                    # "full_text": row.get('text', ''),
                    "source": "FinDER_HF" if not self.use_mock else "FinDER_Mock"
                }
                data.append(item)
                
            return data

        except Exception as e:
            print(f"Error processing data: {e}")
            return []
