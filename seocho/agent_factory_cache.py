"""
Agent factory cache — keep IndexingAgent / QueryAgent / SupervisorAgent
hot per ontology version.

Closes seocho-6c9v. Today every Session lazily creates its own agent
instances on first ``add()`` / ``ask()`` (``seocho/session.py:130-150``).
For long-lived processes serving many Sessions (FastAPI workers,
batch ingest pipelines), each Session pays the per-agent creation
cost — ~50-100 ms on a warm process.

The ``AgentFactoryCache`` keys agent instances by:
``(role, ontology_identity_hash, ontology_profile, agent_config_hash)``.
Subsequent Sessions with the same tuple reuse the warmed agent
instead of rebuilding. Bounded by an LRU and a TTL so long-lived
processes don't accumulate stale agents indefinitely.

The cache is opt-in: a Session can pass an existing
``AgentFactoryCache`` via the ``agent_factory_cache`` parameter, or
the module-level singleton can be used for process-wide reuse.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple


def _hash_agent_config(agent_config: Any) -> str:
    """Deterministic short hash for an AgentConfig (or None)."""
    if agent_config is None:
        return ""
    try:
        # AgentConfig has to_dict() — see seocho.agent_config
        payload = agent_config.to_dict() if hasattr(agent_config, "to_dict") else dict(agent_config)
    except Exception:
        return repr(agent_config)[:32]
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


CacheKey = Tuple[str, str, str, str]
"""(role, ontology_identity_hash, ontology_profile, agent_config_hash)"""


@dataclass
class _CacheEntry:
    agent: Any
    created_at: float


class AgentFactoryCache:
    """LRU cache for warmed agent instances.

    Parameters
    ----------
    max_entries:
        Maximum cached agents. Older entries are evicted in LRU order.
    ttl_seconds:
        Entries older than this are evicted on access. ``0`` disables
        TTL eviction (LRU only).
    """

    def __init__(self, *, max_entries: int = 64, ttl_seconds: float = 3600.0) -> None:
        self._cache: "OrderedDict[CacheKey, _CacheEntry]" = OrderedDict()
        self._lock = threading.RLock()
        self.max_entries = int(max_entries)
        self.ttl_seconds = float(ttl_seconds)
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(
        *,
        role: str,
        ontology_identity_hash: str,
        ontology_profile: str = "default",
        agent_config: Any = None,
    ) -> CacheKey:
        return (
            str(role),
            str(ontology_identity_hash or ""),
            str(ontology_profile or "default"),
            _hash_agent_config(agent_config),
        )

    def get(self, key: CacheKey) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self.misses += 1
                return None
            if self.ttl_seconds > 0 and (time.monotonic() - entry.created_at) > self.ttl_seconds:
                self._cache.pop(key, None)
                self.misses += 1
                return None
            # LRU recency bump
            self._cache.move_to_end(key)
            self.hits += 1
            return entry.agent

    def set(self, key: CacheKey, agent: Any) -> None:
        with self._lock:
            self._cache[key] = _CacheEntry(agent=agent, created_at=time.monotonic())
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)

    def get_or_create(
        self,
        key: CacheKey,
        factory: Callable[[], Any],
    ) -> Any:
        """Look up the cached agent or call ``factory`` to build a fresh one."""
        cached = self.get(key)
        if cached is not None:
            return cached
        agent = factory()
        self.set(key, agent)
        return agent

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            hit_ratio = (self.hits / total) if total else 0.0
            return {
                "size": len(self._cache),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
                "hits": self.hits,
                "misses": self.misses,
                "hit_ratio": hit_ratio,
            }


# Process-wide singleton — opt-in: callers explicitly use this when they
# want cross-Session agent reuse.
_DEFAULT_CACHE = AgentFactoryCache()


def get_default_agent_factory_cache() -> AgentFactoryCache:
    """Return the module-level :class:`AgentFactoryCache` singleton."""
    return _DEFAULT_CACHE
