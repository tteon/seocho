import logging
import time
import pandas as pd
from typing import List, Dict

logger = logging.getLogger(__name__)

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
            logger.info("Generating mock data (simulating FinDER dataset structure)...")
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
            logger.info("Loading dataset from %s...", self.dataset_url)
            try:
                from datasets import load_dataset

                if "hf://datasets/" in self.dataset_url:
                     df = pd.read_parquet(self.dataset_url)
                else:
                    ds = load_dataset(self.dataset_url, split="train")
                    df = ds.to_pandas()

            except Exception as e:
                logger.error("Error loading dataset: %s", e)
                return []

        # Common Processing Logic
        try:
            if 'category' not in df.columns:
                logger.info("Refining schema: 'category' column missing, defaulting to 'general'.")
                df['category'] = 'general'

            if 'references' not in df.columns:
                potential_cols = ['text', 'content', 'document']
                for c in potential_cols:
                    if c in df.columns:
                        df['references'] = df[c]
                        break

            if 'references' not in df.columns:
                 logger.error("Could not find content column. Available: %s", list(df.columns))
                 return []

            if 'category' in df.columns and self.target_categories:
                 filtered_df = df[df['category'].isin(self.target_categories)]
                 if len(filtered_df) == 0:
                     logger.warning("Filtering resulted in 0 rows. Using all data instead.")
                     filtered_df = df
            else:
                 filtered_df = df

            logger.info("Processing %d rows.", len(filtered_df))

            data = []
            for idx, row in filtered_df.iterrows():
                doc_id = row.get('_id', row.get('id', f"doc_{idx}"))

                content_raw = row['references']
                if isinstance(content_raw, list) or hasattr(content_raw, 'tolist'):
                    content = "\n".join([str(r) for r in content_raw])
                else:
                    content = str(content_raw)

                item = {
                    "id": str(doc_id)[:50],
                    "content": content,
                    "category": row.get('category', 'general'),
                    "source": "FinDER_HF" if not self.use_mock else "FinDER_Mock"
                }
                data.append(item)

            return data

        except Exception as e:
            logger.error("Error processing data: %s", e)
            return []
