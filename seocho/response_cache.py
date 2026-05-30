"""
Persistent response cache keyed by ontology + workspace + question.

Closes seocho-tfql. SessionContext._query_cache (see seocho-9gdm) lives
in memory and dies with the Session. For repeated queries against the
same ontology version, that cache is wasted across processes /
restarts / parallel workers.

This module ships an opt-in :class:`ResponseCache` interface and two
backends:

- :class:`InMemoryResponseCache` — same shape as the persistent backend,
  process-local, useful for tests and single-process workers.
- :class:`JSONLResponseCache` — append-only JSONL file with newest-wins
  semantics. Survives process restarts. Compaction is left to the
  caller (rotate the file periodically).

Cache key: ``(workspace_id, database, ontology_identity_hash,
normalized_query)`` — same shape as SessionContext._query_cache (post
seocho-9gdm), so results from one Session can satisfy queries from a
fresh Session under the same workspace + ontology version.
"""

from __future__ import annotations

import json
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


CacheKey = Tuple[str, str, str, str]
"""(workspace_id, database, ontology_identity_hash, normalized_question)"""


def _normalize(question: str) -> str:
    return question.strip().lower()


def make_response_cache_key(
    question: str,
    *,
    workspace_id: str = "",
    database: str = "",
    ontology_identity_hash: str = "",
) -> CacheKey:
    return (
        str(workspace_id or ""),
        str(database or ""),
        str(ontology_identity_hash or ""),
        _normalize(question),
    )


@dataclass
class CachedResponse:
    answer: str
    written_at: float
    metadata: Dict[str, Any]


class ResponseCache(ABC):
    """Persistent cache for Session.ask answers."""

    @abstractmethod
    def get(self, key: CacheKey) -> Optional[CachedResponse]:
        """Look up a cached response or return ``None``."""

    @abstractmethod
    def put(self, key: CacheKey, answer: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Persist ``answer`` for ``key``. Newer values overwrite older."""

    def clear(self) -> None:
        """Drop all entries. Default no-op; backends override."""


class InMemoryResponseCache(ResponseCache):
    """Process-local response cache."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: Dict[CacheKey, CachedResponse] = {}

    def get(self, key: CacheKey) -> Optional[CachedResponse]:
        with self._lock:
            return self._store.get(key)

    def put(self, key: CacheKey, answer: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._store[key] = CachedResponse(
                answer=str(answer),
                written_at=time.time(),
                metadata=dict(metadata or {}),
            )

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class JSONLResponseCache(ResponseCache):
    """Append-only JSONL response cache.

    Each line is a JSON object with the cache key tuple flattened.
    Reads load the entire file into memory (newest-wins by file order).
    Production use cases with millions of entries should subclass and
    swap the storage layer for a real KV store; this implementation is
    aimed at single-host workers and tests.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._index: Dict[CacheKey, CachedResponse] = {}
        self._file_offset: int = 0
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    def _load_index(self) -> Dict[CacheKey, CachedResponse]:
        if not os.path.exists(self._path):
            if self._file_offset > 0:
                self._index.clear()
                self._file_offset = 0
            return self._index

        try:
            current_size = os.path.getsize(self._path)
            if current_size < self._file_offset:
                self._index.clear()
                self._file_offset = 0

            if current_size == self._file_offset:
                return self._index

            with open(self._path, "r", encoding="utf-8") as fh:
                fh.seek(self._file_offset)
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = (
                        str(rec.get("workspace_id", "")),
                        str(rec.get("database", "")),
                        str(rec.get("ontology_identity_hash", "")),
                        str(rec.get("question", "")),
                    )
                    self._index[key] = CachedResponse(
                        answer=str(rec.get("answer", "")),
                        written_at=float(rec.get("written_at", 0.0)),
                        metadata=dict(rec.get("metadata") or {}),
                    )
                self._file_offset = fh.tell()
        except OSError:
            pass
        return self._index

    def get(self, key: CacheKey) -> Optional[CachedResponse]:
        with self._lock:
            return self._load_index().get(key)

    def put(self, key: CacheKey, answer: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        record = {
            "workspace_id": key[0],
            "database": key[1],
            "ontology_identity_hash": key[2],
            "question": key[3],
            "answer": str(answer),
            "written_at": time.time(),
            "metadata": dict(metadata or {}),
        }
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")

    def clear(self) -> None:
        with self._lock:
            try:
                os.unlink(self._path)
            except FileNotFoundError:
                pass
