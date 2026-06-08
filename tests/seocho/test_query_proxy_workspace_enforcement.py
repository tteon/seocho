"""Fitness function for tenant isolation at the runtime query boundary (seocho-a6d).

Pins two invariants:
1. Default (enforcement off): QueryProxy calls the store with the exact, narrow
   signature backends/test-doubles already accept — no behaviour change.
2. Enforcement on: QueryProxy threads workspace_id and enforce_workspace_filter
   to the store, so a query whose Cypher does not scope to $workspace_id is
   refused. This makes tenant isolation a single deployment flag
   (SEOCHO_ENFORCE_WORKSPACE_FILTER) rather than a per-call opt-in nobody used.
"""

from __future__ import annotations

import pytest

from seocho.query.query_proxy import QueryProxy, QueryRequest


class _WorkspaceFilterMissing(RuntimeError):
    pass


class _StrictStore:
    """Mimics a backend that ONLY accepts the narrow query signature.

    If QueryProxy passed workspace_id / enforce_workspace_filter on the default
    path, this would raise TypeError — that's the regression guard.
    """

    def query(self, cypher, *, params=None, database="neo4j"):
        return [{"ok": True}]


class _EnforcingStore:
    """Mimics Neo4jGraphStore.query: accepts the isolation kwargs and refuses
    Cypher that doesn't reference $workspace_id when enforcement is requested."""

    def __init__(self):
        self.calls = []

    def query(self, cypher, *, params=None, database="neo4j",
              workspace_id=None, enforce_workspace_filter=False):
        self.calls.append({
            "cypher": cypher, "workspace_id": workspace_id,
            "enforce_workspace_filter": enforce_workspace_filter,
        })
        if enforce_workspace_filter and "$workspace_id" not in cypher:
            raise _WorkspaceFilterMissing("cypher must reference $workspace_id")
        return [{"ok": True}]


def _req(cypher):
    return QueryRequest(cypher=cypher, workspace_id="acme", database="neo4j")


def test_default_off_uses_narrow_signature():
    proxy = QueryProxy(_StrictStore(), enforce_workspace_filter=False)
    # Must not raise TypeError — i.e. no extra kwargs leak to the backend.
    assert proxy.query(_req("MATCH (n) RETURN n")) == [{"ok": True}]


def test_enforcement_threads_workspace_and_flag():
    store = _EnforcingStore()
    proxy = QueryProxy(store, enforce_workspace_filter=True)
    proxy.query(_req("MATCH (n) WHERE n._workspace_id = $workspace_id RETURN n"))
    assert store.calls[-1]["workspace_id"] == "acme"
    assert store.calls[-1]["enforce_workspace_filter"] is True


def test_enforcement_refuses_unscoped_cypher():
    proxy = QueryProxy(_EnforcingStore(), enforce_workspace_filter=True)
    with pytest.raises(_WorkspaceFilterMissing):
        proxy.query(_req("MATCH (n) RETURN n"))


def test_default_taken_from_env(monkeypatch):
    monkeypatch.setenv("SEOCHO_ENFORCE_WORKSPACE_FILTER", "1")
    proxy = QueryProxy(_EnforcingStore())  # no explicit flag -> env default
    assert proxy._enforce_workspace_filter is True
    monkeypatch.setenv("SEOCHO_ENFORCE_WORKSPACE_FILTER", "0")
    assert QueryProxy(_StrictStore())._enforce_workspace_filter is False
