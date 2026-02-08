"""Tests for SharedMemory cache behavior."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared_memory import SharedMemory, MAX_QUERY_CACHE_SIZE


class TestSharedMemoryBasic:
    def test_put_and_get(self):
        mem = SharedMemory()
        mem.put("key1", "value1")
        assert mem.get("key1") == "value1"

    def test_get_missing_key(self):
        mem = SharedMemory()
        assert mem.get("missing") is None
        assert mem.get("missing", "default") == "default"

    def test_cache_query_result(self):
        mem = SharedMemory()
        mem.cache_query_result("kgnormal", "MATCH (n) RETURN n", "[{data}]")
        result = mem.get_cached_query("kgnormal", "MATCH (n) RETURN n")
        assert result == "[{data}]"

    def test_cache_miss(self):
        mem = SharedMemory()
        assert mem.get_cached_query("kgnormal", "MATCH (n) RETURN n") is None

    def test_get_all_results(self):
        mem = SharedMemory()
        mem.put("a", 1)
        mem.put("b", 2)
        results = mem.get_all_results()
        assert results == {"a": 1, "b": 2}

    def test_cache_key_normalization(self):
        """Queries differing only in whitespace/case should share cache key."""
        mem = SharedMemory()
        mem.cache_query_result("db", "  MATCH (n)  RETURN n  ", "result")
        assert mem.get_cached_query("db", "match (n)  return n") == "result"


class TestSharedMemoryEviction:
    def test_eviction_at_capacity(self):
        mem = SharedMemory()

        # Fill cache to capacity
        for i in range(MAX_QUERY_CACHE_SIZE):
            mem.cache_query_result("db", f"query_{i}", f"result_{i}")

        assert len(mem._query_cache) == MAX_QUERY_CACHE_SIZE

        # Add one more — should evict the oldest
        mem.cache_query_result("db", "query_overflow", "overflow_result")
        assert len(mem._query_cache) == MAX_QUERY_CACHE_SIZE

        # First query should be evicted
        assert mem.get_cached_query("db", "query_0") is None
        # Last query should be present
        assert mem.get_cached_query("db", "query_overflow") == "overflow_result"

    def test_lru_access_prevents_eviction(self):
        mem = SharedMemory()

        # Fill cache
        for i in range(MAX_QUERY_CACHE_SIZE):
            mem.cache_query_result("db", f"query_{i}", f"result_{i}")

        # Access query_0 to make it most recently used
        mem.get_cached_query("db", "query_0")

        # Add one more — should evict query_1 (now oldest), not query_0
        mem.cache_query_result("db", "query_new", "new_result")

        assert mem.get_cached_query("db", "query_0") == "result_0"
        assert mem.get_cached_query("db", "query_1") is None
