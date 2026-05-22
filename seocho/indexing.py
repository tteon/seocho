"""Backward-compatible re-export — canonical location is ``seocho.index.pipeline``."""
from seocho.index.chunk import Chunk, build_chunk_id, chunk  # noqa: F401
from seocho.index.pipeline import (  # noqa: F401
    BatchIndexingResult,
    IndexingPipeline,
    IndexingResult,
    chunk_text,
    content_hash,
)

__all__ = [
    "IndexingPipeline",
    "IndexingResult",
    "BatchIndexingResult",
    "Chunk",
    "chunk",
    "build_chunk_id",
    "chunk_text",
    "content_hash",
]
