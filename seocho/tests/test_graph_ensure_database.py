"""Regression tests for Neo4jGraphStore.ensure_database online-wait.

DozerDB / Neo4j ``CREATE DATABASE`` is asynchronous; the SDK must wait until the
database reports ONLINE before returning, or immediate writes hit "Graph not
found". No live DozerDB — a fake driver scripts the status poll; _sleep is
monkeypatched so the poll loop runs instantly.
"""
from __future__ import annotations

import pytest

import seocho.store.graph as g
from seocho.store.graph import DatabaseNameError, Neo4jGraphStore


class _FakeSession:
    def __init__(self, drv: "_FakeDriver") -> None:
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query: str, **kw):
        self.drv.calls.append(query)
        if query.startswith("SHOW DATABASES YIELD"):
            status = self.drv.statuses.pop(0) if self.drv.statuses else "online"
            return [{"status": status}]
        if query.startswith("SHOW DATABASES"):
            return [{"name": n} for n in self.drv.existing]
        if query.startswith("CREATE DATABASE"):
            self.drv.created.append(query)
            return []
        return []


class _FakeDriver:
    def __init__(self, statuses, existing=()):
        self.statuses = list(statuses)
        self.existing = set(existing)
        self.created: list[str] = []
        self.calls: list[str] = []

    def session(self, database=None):
        return _FakeSession(self)


def _store(driver) -> Neo4jGraphStore:
    store = Neo4jGraphStore.__new__(Neo4jGraphStore)  # bypass real driver connect
    store._driver = driver
    return store


def test_ensure_database_blocks_until_online(monkeypatch):
    monkeypatch.setattr(g, "_sleep", lambda *_: None)
    drv = _FakeDriver(statuses=["creating", "creating", "online"])
    store = _store(drv)

    created = store.ensure_database("expdb", wait_online=True, timeout=5.0)

    assert created is True                       # not in existing -> created
    assert any(c.startswith("CREATE DATABASE") for c in drv.calls)
    # It polled SHOW DATABASES YIELD until ONLINE (consumed the "creating" statuses).
    assert sum(1 for c in drv.calls if c.startswith("SHOW DATABASES YIELD")) >= 3


def test_ensure_database_existing_returns_false_but_still_waits_online(monkeypatch):
    monkeypatch.setattr(g, "_sleep", lambda *_: None)
    drv = _FakeDriver(statuses=["online"], existing=["expdb"])
    store = _store(drv)

    created = store.ensure_database("expdb", wait_online=True, timeout=5.0)

    assert created is False                      # already existed
    assert not any(c.startswith("CREATE DATABASE") for c in drv.calls)
    assert any(c.startswith("SHOW DATABASES YIELD") for c in drv.calls)


def test_ensure_database_timeout_does_not_hang(monkeypatch):
    monkeypatch.setattr(g, "_sleep", lambda *_: None)
    drv = _FakeDriver(statuses=["creating"] * 50, existing=["expdb"])
    store = _store(drv)

    # timeout=0 -> one poll, status still "creating", returns without hanging.
    created = store.ensure_database("expdb", wait_online=True, timeout=0.0)
    assert created is False


def test_ensure_database_wait_online_false_skips_poll():
    drv = _FakeDriver(statuses=[], existing=[])
    store = _store(drv)

    store.ensure_database("expdb", wait_online=False)
    assert not any(c.startswith("SHOW DATABASES YIELD") for c in drv.calls)


def test_ensure_database_invalid_name_raises():
    store = _store(_FakeDriver([]))
    with pytest.raises(DatabaseNameError):
        store.ensure_database("Invalid-Name!")
