"""
seocho.index — Data Plane: indexing, extraction, and graph construction.

Where to look:
- ``pipeline``: Chunking → extraction → SHACL validation → dedup → graph write
- ``file_reader``: Read .txt/.md/.csv/.json/.jsonl and index them

If you want to improve extraction quality, start here.
"""

from .pipeline import (
    BatchIndexingResult,
    IndexingPipeline,
    IndexingResult,
    chunk_text,
    content_hash,
)
from .extraction_engine import CanonicalExtractionEngine
from .ingestion_facade import IngestRequest, IngestionFacade
from .file_reader import (
    DirectoryIndexResult,
    FileIndexer,
    FileIndexResult,
    FileTracker,
    SUPPORTED_EXTENSIONS,
)

__all__ = [
    "IndexingPipeline",
    "CanonicalExtractionEngine",
    "IngestRequest",
    "IngestionFacade",
    "IndexingResult",
    "BatchIndexingResult",
    "chunk_text",
    "content_hash",
    "FileIndexer",
    "FileIndexResult",
    "DirectoryIndexResult",
    "FileTracker",
    "SUPPORTED_EXTENSIONS",
]
