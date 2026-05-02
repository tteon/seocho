"""Regression tests for seocho-tfql — persistent response cache."""

from __future__ import annotations

import os

import pytest


def test_cache_key_shape() -> None:
    from seocho.response_cache import make_response_cache_key
    key = make_response_cache_key(
        " WHO Is the CEO?  ",
        workspace_id="acme",
        database="prod",
        ontology_identity_hash="h1",
    )
    assert key == ("acme", "prod", "h1", "who is the ceo?")


def test_inmemory_get_put() -> None:
    from seocho.response_cache import InMemoryResponseCache, make_response_cache_key
    cache = InMemoryResponseCache()
    key = make_response_cache_key("q?", workspace_id="A")
    assert cache.get(key) is None
    cache.put(key, "answer", metadata={"source": "test"})
    cached = cache.get(key)
    assert cached is not None
    assert cached.answer == "answer"
    assert cached.metadata["source"] == "test"


def test_inmemory_clear() -> None:
    from seocho.response_cache import InMemoryResponseCache, make_response_cache_key
    cache = InMemoryResponseCache()
    key = make_response_cache_key("q?", workspace_id="A")
    cache.put(key, "answer")
    cache.clear()
    assert cache.get(key) is None


def test_jsonl_persists_across_instances(tmp_path) -> None:
    """Putting in one cache instance should be visible from another reading the same file."""
    from seocho.response_cache import JSONLResponseCache, make_response_cache_key
    path = tmp_path / "cache.jsonl"
    cache_a = JSONLResponseCache(str(path))
    key = make_response_cache_key("who?", workspace_id="A", ontology_identity_hash="h1")
    cache_a.put(key, "Tim Cook", metadata={"src": "a"})

    cache_b = JSONLResponseCache(str(path))
    cached = cache_b.get(key)
    assert cached is not None
    assert cached.answer == "Tim Cook"
    assert cached.metadata["src"] == "a"


def test_jsonl_newest_wins(tmp_path) -> None:
    """When the same key is written twice, get() returns the latest answer."""
    from seocho.response_cache import JSONLResponseCache, make_response_cache_key
    path = tmp_path / "cache.jsonl"
    cache = JSONLResponseCache(str(path))
    key = make_response_cache_key("q?", workspace_id="A")
    cache.put(key, "first")
    cache.put(key, "second")
    cached = cache.get(key)
    assert cached is not None
    assert cached.answer == "second"


def test_jsonl_workspace_isolation(tmp_path) -> None:
    """Same question, different workspace → independent answers."""
    from seocho.response_cache import JSONLResponseCache, make_response_cache_key
    path = tmp_path / "cache.jsonl"
    cache = JSONLResponseCache(str(path))
    key_a = make_response_cache_key("q?", workspace_id="A")
    key_b = make_response_cache_key("q?", workspace_id="B")
    cache.put(key_a, "alpha-answer")
    cache.put(key_b, "beta-answer")
    assert cache.get(key_a).answer == "alpha-answer"
    assert cache.get(key_b).answer == "beta-answer"


def test_jsonl_clear_removes_file(tmp_path) -> None:
    from seocho.response_cache import JSONLResponseCache, make_response_cache_key
    path = tmp_path / "cache.jsonl"
    cache = JSONLResponseCache(str(path))
    cache.put(make_response_cache_key("q", workspace_id="A"), "ans")
    assert os.path.exists(str(path))
    cache.clear()
    assert not os.path.exists(str(path))


def test_jsonl_handles_missing_file(tmp_path) -> None:
    from seocho.response_cache import JSONLResponseCache, make_response_cache_key
    path = tmp_path / "missing.jsonl"
    cache = JSONLResponseCache(str(path))
    # File doesn't exist yet — get should return None, not raise
    assert cache.get(make_response_cache_key("q", workspace_id="A")) is None


def test_jsonl_skips_corrupt_lines(tmp_path) -> None:
    from seocho.response_cache import JSONLResponseCache, make_response_cache_key
    path = tmp_path / "corrupt.jsonl"
    # Manually write a corrupt line
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"this is not valid json\n')
        fh.write(
            '{"workspace_id": "A", "database": "", "ontology_identity_hash": "", '
            '"question": "q", "answer": "valid", "written_at": 0, "metadata": {}}\n'
        )
    cache = JSONLResponseCache(str(path))
    cached = cache.get(make_response_cache_key("q", workspace_id="A"))
    assert cached is not None and cached.answer == "valid"
