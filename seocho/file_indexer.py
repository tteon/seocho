"""
File-based indexing — drop files into a directory and index them.

The simplest way to get your data into SEOCHO::

    from seocho import Seocho, Ontology, NodeDef, P
    from seocho.graph_store import Neo4jGraphStore
    from seocho.llm_backend import OpenAIBackend

    s = Seocho(ontology=onto, graph_store=store, llm=llm)

    # Index everything in a directory
    results = s.index_directory("./my_data/")

    # Index a single file
    result = s.index_file("./report.txt")

    # Re-index when a file changes
    result = s.index_file("./report.txt", force=True)

Supported formats:

- ``.txt`` — plain text (one document)
- ``.md`` — markdown (one document)
- ``.csv`` — each row becomes a separate document
- ``.json`` — array of objects with ``content`` field
- ``.jsonl`` — one JSON object per line with ``content`` field
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from .indexing import IndexingPipeline, IndexingResult

logger = logging.getLogger(__name__)

# Supported extensions
SUPPORTED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".jsonl"}


@dataclass
class FileIndexResult:
    """Result of indexing a single file."""

    path: str
    status: str  # "indexed", "skipped", "failed", "unchanged"
    indexing_result: Optional[IndexingResult] = None
    records_found: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "records_found": self.records_found,
            "error": self.error,
            "indexing": self.indexing_result.to_dict() if self.indexing_result else None,
        }


@dataclass
class DirectoryIndexResult:
    """Result of indexing a directory."""

    directory: str
    files_found: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    files_unchanged: int = 0
    results: List[FileIndexResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.files_failed == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "directory": self.directory,
            "files_found": self.files_found,
            "files_indexed": self.files_indexed,
            "files_skipped": self.files_skipped,
            "files_failed": self.files_failed,
            "files_unchanged": self.files_unchanged,
            "ok": self.ok,
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# File tracking — knows which files have been indexed and when
# ---------------------------------------------------------------------------

_TRACKING_FILE = ".seocho_index"


@dataclass
class _FileState:
    path: str
    mtime: float
    size: int
    source_id: str
    content_hash: str


class FileTracker:
    """Tracks which files have been indexed (path + mtime + hash).

    Persists to a ``.seocho_index`` JSON file in the indexed directory.
    """

    def __init__(self, directory: Union[str, Path]) -> None:
        self.directory = Path(directory)
        self._tracking_path = self.directory / _TRACKING_FILE
        self._states: Dict[str, _FileState] = {}
        self._load()

    def _load(self) -> None:
        if self._tracking_path.exists():
            try:
                data = json.loads(self._tracking_path.read_text())
                for entry in data.get("files", []):
                    state = _FileState(**entry)
                    self._states[state.path] = state
            except (json.JSONDecodeError, TypeError, KeyError):
                logger.warning("Corrupt tracking file %s, starting fresh", self._tracking_path)
                self._states = {}

    def save(self) -> None:
        data = {
            "version": 1,
            "files": [
                {
                    "path": s.path,
                    "mtime": s.mtime,
                    "size": s.size,
                    "source_id": s.source_id,
                    "content_hash": s.content_hash,
                }
                for s in self._states.values()
            ],
        }
        self._tracking_path.write_text(json.dumps(data, indent=2))

    def needs_indexing(self, path: Path) -> bool:
        """Check if a file is new or changed since last indexing."""
        key = str(path)
        if key not in self._states:
            return True
        state = self._states[key]
        stat = path.stat()
        return stat.st_mtime != state.mtime or stat.st_size != state.size

    def mark_indexed(self, path: Path, source_id: str, content_hash: str) -> None:
        stat = path.stat()
        self._states[str(path)] = _FileState(
            path=str(path),
            mtime=stat.st_mtime,
            size=stat.st_size,
            source_id=source_id,
            content_hash=content_hash,
        )

    def get_source_id(self, path: Path) -> Optional[str]:
        state = self._states.get(str(path))
        return state.source_id if state else None

    def remove(self, path: Path) -> Optional[str]:
        key = str(path)
        state = self._states.pop(key, None)
        return state.source_id if state else None


# ---------------------------------------------------------------------------
# File readers
# ---------------------------------------------------------------------------

def read_text_file(path: Path) -> List[Dict[str, Any]]:
    """Read a .txt or .md file as a single document."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return [{"content": text, "metadata": {"source_file": str(path), "format": path.suffix}}]


def read_csv_file(path: Path) -> List[Dict[str, Any]]:
    """Read a .csv file — each row with a 'content' column becomes a document.

    If no 'content' column exists, all columns are joined as text.
    """
    records: List[Dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if "content" in row:
                content = row["content"]
                meta = {k: v for k, v in row.items() if k != "content"}
            else:
                content = " | ".join(f"{k}: {v}" for k, v in row.items() if v)
                meta = dict(row)
            meta["source_file"] = str(path)
            meta["row_index"] = i
            records.append({"content": content, "metadata": meta})
    return records


def read_json_file(path: Path) -> List[Dict[str, Any]]:
    """Read a .json file — expects an array of objects with 'content' field."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        records = []
        for i, item in enumerate(data):
            if isinstance(item, dict):
                content = item.get("content", json.dumps(item))
                meta = {k: v for k, v in item.items() if k != "content"}
                meta["source_file"] = str(path)
                meta["item_index"] = i
                records.append({"content": content, "metadata": meta})
        return records
    elif isinstance(data, dict) and "content" in data:
        return [{"content": data["content"], "metadata": {"source_file": str(path)}}]
    else:
        return [{"content": json.dumps(data), "metadata": {"source_file": str(path)}}]


def read_jsonl_file(path: Path) -> List[Dict[str, Any]]:
    """Read a .jsonl file — one JSON object per line."""
    records: List[Dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                content = item.get("content", json.dumps(item))
                meta = {k: v for k, v in item.items() if k != "content"}
                meta["source_file"] = str(path)
                meta["line_index"] = i
                records.append({"content": content, "metadata": meta})
        except json.JSONDecodeError:
            logger.warning("Skipping malformed JSON at %s line %d", path, i)
    return records


FILE_READERS = {
    ".txt": read_text_file,
    ".md": read_text_file,
    ".csv": read_csv_file,
    ".json": read_json_file,
    ".jsonl": read_jsonl_file,
}


# ---------------------------------------------------------------------------
# File indexer
# ---------------------------------------------------------------------------

class FileIndexer:
    """Indexes files from disk into the knowledge graph.

    Parameters
    ----------
    pipeline:
        The indexing pipeline to use.
    database:
        Default target database.
    category:
        Default document category.
    extensions:
        File extensions to index.  Defaults to all supported types.
    """

    def __init__(
        self,
        pipeline: IndexingPipeline,
        *,
        database: str = "neo4j",
        category: str = "file",
        extensions: Optional[set] = None,
    ) -> None:
        self.pipeline = pipeline
        self.database = database
        self.category = category
        self.extensions = extensions or SUPPORTED_EXTENSIONS

    def index_file(
        self,
        path: Union[str, Path],
        *,
        database: Optional[str] = None,
        category: Optional[str] = None,
        force: bool = False,
        tracker: Optional[FileTracker] = None,
    ) -> FileIndexResult:
        """Index a single file.

        Parameters
        ----------
        path:
            Path to the file.
        database:
            Target database (overrides default).
        category:
            Document category (overrides default).
        force:
            If True, re-index even if the file hasn't changed.
        tracker:
            File tracker for incremental indexing.

        Returns
        -------
        FileIndexResult with status and metrics.
        """
        path = Path(path)
        db = database or self.database
        cat = category or self.category

        if not path.exists():
            return FileIndexResult(path=str(path), status="failed", error="File not found")

        if path.suffix.lower() not in self.extensions:
            return FileIndexResult(
                path=str(path), status="skipped",
                error=f"Unsupported format: {path.suffix}",
            )

        # Check if file changed
        if tracker and not force and not tracker.needs_indexing(path):
            return FileIndexResult(path=str(path), status="unchanged")

        # If re-indexing, delete old data first
        if tracker and force:
            old_source_id = tracker.get_source_id(path)
            if old_source_id:
                try:
                    self.pipeline.delete_source(old_source_id, database=db)
                except Exception as exc:
                    logger.warning("Could not delete old source %s: %s", old_source_id, exc)

        # Read file
        reader = FILE_READERS.get(path.suffix.lower())
        if reader is None:
            return FileIndexResult(path=str(path), status="failed", error="No reader for format")

        try:
            records = reader(path)
        except Exception as exc:
            return FileIndexResult(path=str(path), status="failed", error=f"Read error: {exc}")

        if not records:
            return FileIndexResult(path=str(path), status="skipped", error="No content found")

        # Index each record
        total_result = IndexingResult()
        for record in records:
            content = record.get("content", "")
            metadata = record.get("metadata", {})
            if not content.strip():
                continue

            result = self.pipeline.index(
                content,
                database=db,
                category=cat,
                metadata=metadata,
            )
            total_result.chunks_processed += result.chunks_processed
            total_result.total_nodes += result.total_nodes
            total_result.total_relationships += result.total_relationships
            total_result.validation_errors.extend(result.validation_errors)
            total_result.write_errors.extend(result.write_errors)
            total_result.skipped_chunks += result.skipped_chunks
            if not total_result.source_id:
                total_result.source_id = result.source_id

        # Track
        if tracker:
            from .indexing import content_hash as _hash
            full_text = path.read_text(encoding="utf-8", errors="replace")
            tracker.mark_indexed(path, total_result.source_id, _hash(full_text))

        return FileIndexResult(
            path=str(path),
            status="indexed" if total_result.ok else "failed",
            indexing_result=total_result,
            records_found=len(records),
            error="; ".join(total_result.write_errors) if total_result.write_errors else None,
        )

    def index_directory(
        self,
        directory: Union[str, Path],
        *,
        database: Optional[str] = None,
        category: Optional[str] = None,
        recursive: bool = True,
        force: bool = False,
        on_file: Optional[Callable[[str, int, int], None]] = None,
    ) -> DirectoryIndexResult:
        """Index all supported files in a directory.

        Parameters
        ----------
        directory:
            Path to the directory.
        recursive:
            If True, scan subdirectories too.
        force:
            If True, re-index all files (ignore change tracking).
        on_file:
            Progress callback ``(file_path, current, total)``.

        Returns
        -------
        DirectoryIndexResult with per-file results.
        """
        directory = Path(directory)
        if not directory.is_dir():
            return DirectoryIndexResult(directory=str(directory))

        tracker = FileTracker(directory)

        # Discover files
        files: List[Path] = []
        if recursive:
            for root, _, filenames in os.walk(directory):
                for fname in sorted(filenames):
                    fpath = Path(root) / fname
                    if fpath.suffix.lower() in self.extensions and fpath.name != _TRACKING_FILE:
                        files.append(fpath)
        else:
            files = sorted(
                f for f in directory.iterdir()
                if f.is_file() and f.suffix.lower() in self.extensions
            )

        result = DirectoryIndexResult(
            directory=str(directory),
            files_found=len(files),
        )

        for i, fpath in enumerate(files):
            if on_file:
                on_file(str(fpath), i, len(files))

            file_result = self.index_file(
                fpath,
                database=database,
                category=category,
                force=force,
                tracker=tracker,
            )
            result.results.append(file_result)

            if file_result.status == "indexed":
                result.files_indexed += 1
            elif file_result.status == "skipped":
                result.files_skipped += 1
            elif file_result.status == "failed":
                result.files_failed += 1
            elif file_result.status == "unchanged":
                result.files_unchanged += 1

        tracker.save()
        return result
