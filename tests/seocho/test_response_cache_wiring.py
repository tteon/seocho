"""Tests for the persistent response cache + its env switch (seocho-jdg).

The cross-process reuse mechanism (the council's synergy #1) is proven here:
an answer written by one cache instance is served by a fresh instance over the
same file — i.e. a fresh Session/process/worker gets the hit.
"""

from __future__ import annotations

from seocho.response_cache import (
    JSONLResponseCache,
    make_response_cache_key,
    response_cache_from_env,
)


def test_env_switch_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SEOCHO_RESPONSE_CACHE_PATH", raising=False)
    assert response_cache_from_env() is None


def test_env_switch_builds_jsonl_cache(tmp_path, monkeypatch):
    path = str(tmp_path / "rc.jsonl")
    monkeypatch.setenv("SEOCHO_RESPONSE_CACHE_PATH", path)
    cache = response_cache_from_env()
    assert isinstance(cache, JSONLResponseCache)


def test_key_shape_matches_session_tuple():
    key = make_response_cache_key("  What is X? ", workspace_id="acme", database="finance",
                                  ontology_identity_hash="h1")
    # (workspace, database, ontology_hash, normalized_question)
    assert key == ("acme", "finance", "h1", "what is x?")


def test_cross_process_reuse(tmp_path):
    path = str(tmp_path / "rc.jsonl")
    key = make_response_cache_key("q", workspace_id="acme", database="finance",
                                  ontology_identity_hash="h1")
    # writer process
    JSONLResponseCache(path).put(key, "the answer", metadata={"database": "finance"})
    # a FRESH instance (simulating another process/worker) gets the hit
    hit = JSONLResponseCache(path).get(key)
    assert hit is not None and hit.answer == "the answer"
    # a different ontology version (different hash) does NOT collide
    other = make_response_cache_key("q", workspace_id="acme", database="finance",
                                    ontology_identity_hash="h2")
    assert JSONLResponseCache(path).get(other) is None
