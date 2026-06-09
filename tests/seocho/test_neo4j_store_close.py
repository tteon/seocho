"""Regression for #135 — Neo4jGraphStore must release its driver connection
pool deterministically. It opened a driver in __init__ but had no context
manager, no __del__, and nothing cascaded a close(), so per-request/per-
workspace stores leaked pools for the process lifetime.
"""

from __future__ import annotations

import sys
import types

import pytest


class _FakeDriver:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


@pytest.fixture
def _fake_neo4j(monkeypatch):
    captured = {}

    def driver(uri, auth=None, **kw):
        captured["driver"] = _FakeDriver()
        return captured["driver"]

    mod = types.ModuleType("neo4j")
    mod.GraphDatabase = types.SimpleNamespace(driver=driver)
    monkeypatch.setitem(sys.modules, "neo4j", mod)
    return captured


def _store():
    from seocho.store.graph import Neo4jGraphStore

    return Neo4jGraphStore("bolt://localhost:7687", "neo4j", "pw")


def test_context_manager_closes_driver(_fake_neo4j):
    with _store():
        pass
    assert _fake_neo4j["driver"].close_calls == 1


def test_close_is_idempotent(_fake_neo4j):
    store = _store()
    store.close()
    store.close()
    assert _fake_neo4j["driver"].close_calls == 1


def test_del_closes_driver_as_safety_net(_fake_neo4j):
    store = _store()
    store.__del__()  # simulate GC finalization
    assert _fake_neo4j["driver"].close_calls == 1


def test_del_after_explicit_close_does_not_double_close(_fake_neo4j):
    store = _store()
    store.close()
    store.__del__()
    assert _fake_neo4j["driver"].close_calls == 1
