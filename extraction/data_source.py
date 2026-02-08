"""
Data Source Module for SEOCHO

Provides a universal DataSource interface for loading data from
CSV, JSON, Parquet files and REST APIs into a standard format.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)


class DataSource(ABC):
    """Universal data source interface.

    All data sources must return records in the standard format:
    [{"id": str, "content": str, "category": str, "source": str, "metadata": dict}]
    """

    @abstractmethod
    def load(self) -> List[Dict[str, Any]]:
        """Load data and return standardised records."""
        ...


class FileDataSource(DataSource):
    """Load data from CSV, JSON, or Parquet files."""

    _SUPPORTED_EXTENSIONS = {".csv", ".json", ".jsonl", ".parquet"}

    def __init__(
        self,
        path: str,
        content_column: str = "content",
        id_column: str = "id",
        category_column: str = "category",
    ):
        self.path = Path(path)
        self.content_column = content_column
        self.id_column = id_column
        self.category_column = category_column

        if self.path.suffix not in self._SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file extension '{self.path.suffix}'. "
                f"Supported: {self._SUPPORTED_EXTENSIONS}"
            )

    def load(self) -> List[Dict[str, Any]]:
        logger.info("Loading file data source: %s", self.path)
        df = self._read_file()
        return self._to_standard_records(df)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_file(self) -> pd.DataFrame:
        ext = self.path.suffix
        if ext == ".csv":
            return pd.read_csv(self.path)
        if ext in (".json", ".jsonl"):
            return pd.read_json(self.path, lines=(ext == ".jsonl"))
        if ext == ".parquet":
            return pd.read_parquet(self.path)
        raise ValueError(f"Unsupported extension: {ext}")

    def _to_standard_records(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        # Resolve content column (try alternatives if primary missing)
        content_col = self._resolve_column(
            df, self.content_column, fallbacks=["text", "document", "references"]
        )
        if content_col is None:
            logger.error(
                "No content column found. Available: %s", list(df.columns)
            )
            return []

        records: List[Dict[str, Any]] = []
        for idx, row in df.iterrows():
            raw_content = row[content_col]
            if isinstance(raw_content, list):
                content = "\n".join(str(r) for r in raw_content)
            else:
                content = str(raw_content)

            doc_id = str(row.get(self.id_column, f"doc_{idx}"))[:50]
            category = str(row.get(self.category_column, "general"))

            records.append(
                {
                    "id": doc_id,
                    "content": content,
                    "category": category,
                    "source": str(self.path.name),
                    "metadata": {},
                }
            )

        logger.info("Loaded %d records from %s", len(records), self.path.name)
        return records

    @staticmethod
    def _resolve_column(
        df: pd.DataFrame, primary: str, fallbacks: List[str]
    ) -> Optional[str]:
        if primary in df.columns:
            return primary
        for col in fallbacks:
            if col in df.columns:
                return col
        return None


class APIDataSource(DataSource):
    """Load data from a REST API endpoint."""

    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        data_path: str = "data",
        content_field: str = "content",
        id_field: str = "id",
        category_field: str = "category",
        timeout: int = 30,
    ):
        self.url = url
        self.headers = headers or {}
        self.params = params or {}
        self.data_path = data_path
        self.content_field = content_field
        self.id_field = id_field
        self.category_field = category_field
        self.timeout = timeout

    def load(self) -> List[Dict[str, Any]]:
        logger.info("Fetching data from API: %s", self.url)
        try:
            resp = requests.get(
                self.url,
                headers=self.headers,
                params=self.params,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("API request failed: %s", e)
            return []

        payload = resp.json()

        # Navigate to data_path (supports dot-notation, e.g. "results.items")
        items = payload
        for key in self.data_path.split("."):
            if isinstance(items, dict):
                items = items.get(key, [])
            else:
                break

        if not isinstance(items, list):
            logger.error("Expected list at data_path '%s', got %s", self.data_path, type(items).__name__)
            return []

        records: List[Dict[str, Any]] = []
        for i, item in enumerate(items):
            content = str(item.get(self.content_field, ""))
            doc_id = str(item.get(self.id_field, f"api_{i}"))[:50]
            category = str(item.get(self.category_field, "general"))

            records.append(
                {
                    "id": doc_id,
                    "content": content,
                    "category": category,
                    "source": self.url,
                    "metadata": {},
                }
            )

        logger.info("Loaded %d records from API", len(records))
        return records
