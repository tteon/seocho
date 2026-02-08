"""
Shared Memory Store

Per-request memory shared across agents during Parallel Debate.
Prevents duplicate Cypher queries and allows agents to share
intermediate results.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class SharedMemory:
    """Agent-shared memory store with request-scoped lifecycle.

    Usage:
        memory = SharedMemory()
        memory.put("agent_result:kgnormal", "...")
        memory.get("agent_result:kgnormal")

        # Query-level caching
        memory.cache_query_result("kgnormal", "MATCH (n) RETURN n LIMIT 5", "[{...}]")
        memory.get_cached_query("kgnormal", "MATCH (n) RETURN n LIMIT 5")
    """

    _store: Dict[str, Any] = field(default_factory=dict)
    _query_cache: Dict[str, str] = field(default_factory=dict)

    def put(self, key: str, value: Any) -> None:
        """Store an intermediate result."""
        self._store[key] = value
        logger.debug("SharedMemory PUT: %s", key)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve an intermediate result."""
        return self._store.get(key, default)

    def cache_query_result(self, db_name: str, query: str, result: str) -> None:
        """Cache a Cypher query result to avoid re-execution."""
        cache_key = self._make_cache_key(db_name, query)
        self._query_cache[cache_key] = result
        logger.debug("SharedMemory CACHE: %s (db=%s)", cache_key[:16], db_name)

    def get_cached_query(self, db_name: str, query: str) -> Optional[str]:
        """Look up a previously cached query result."""
        cache_key = self._make_cache_key(db_name, query)
        return self._query_cache.get(cache_key)

    def get_all_results(self) -> Dict[str, Any]:
        """Return all stored results (used by Supervisor for synthesis)."""
        return dict(self._store)

    @staticmethod
    def _make_cache_key(db_name: str, query: str) -> str:
        normalized = query.strip().lower()
        return f"{db_name}:{hashlib.md5(normalized.encode()).hexdigest()}"
