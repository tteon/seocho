"""Backward-compatible re-export — canonical location is ``seocho.index.file_reader``."""
from seocho.index.file_reader import (  # noqa: F401
    DirectoryIndexResult,
    FileIndexer,
    FileIndexResult,
    FileTracker,
    SUPPORTED_EXTENSIONS,
    read_csv_file,
    read_json_file,
    read_jsonl_file,
    read_text_file,
)

__all__ = [
    "FileIndexer", "FileIndexResult", "DirectoryIndexResult",
    "FileTracker", "SUPPORTED_EXTENSIONS",
    "read_text_file", "read_csv_file", "read_json_file", "read_jsonl_file",
]
