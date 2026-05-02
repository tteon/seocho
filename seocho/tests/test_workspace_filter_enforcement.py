"""Regression tests for seocho-y4at — workspace_id filter enforcement on queries.

Background: writes stamp ``_workspace_id`` on every node and relationship,
but ``query()`` used to run arbitrary Cypher without filtering. Two
workspaces sharing a database leaked data to each other on any
``MATCH (n) RETURN n`` style query.

The fix adds two layers (no risky auto-rewriting of arbitrary Cypher):

1. ``workspace_id`` keyword auto-merges into ``params`` so callers can
   write ``WHERE n._workspace_id = $workspace_id`` without manually
   threading the value.
2. ``enforce_workspace_filter=True`` raises
   :class:`WorkspaceFilterMissingError` when the cypher does not
   reference ``$workspace_id`` — opt-in safety net for multi-tenant
   deployments.
"""

from __future__ import annotations

import pytest


def _build_ladybug_store(tmp_path):
    from seocho.store.graph import LadybugGraphStore
    return LadybugGraphStore(f"{tmp_path}/y4at.lbug")


def test_workspace_id_kwarg_auto_injected_into_params(tmp_path) -> None:
    """workspace_id is auto-added to params so cypher can use $workspace_id."""
    from seocho.store.graph import LadybugGraphStore
    store = _build_ladybug_store(tmp_path)

    captured = {}

    original_execute = store._conn.execute

    def _capture(cypher, params=None):
        captured["cypher"] = cypher
        captured["params"] = params
        # Return a minimal result that won't crash the iteration
        class _Empty:
            column_names = []
            def __iter__(self): return iter([])
        return _Empty()

    store._conn.execute = _capture

    store.query(
        "MATCH (n:Person) WHERE n._workspace_id = $workspace_id RETURN n",
        workspace_id="alpha",
    )

    assert captured["params"].get("workspace_id") == "alpha", (
        f"workspace_id not auto-injected; got params={captured['params']}"
    )


def test_explicit_params_workspace_id_wins_over_kwarg(tmp_path) -> None:
    """If params already has workspace_id, the kwarg does not overwrite it."""
    store = _build_ladybug_store(tmp_path)
    captured = {}

    def _capture(cypher, params=None):
        captured["params"] = params
        class _Empty:
            column_names = []
            def __iter__(self): return iter([])
        return _Empty()

    store._conn.execute = _capture
    store.query(
        "MATCH (n) WHERE n._workspace_id = $workspace_id RETURN n",
        params={"workspace_id": "explicit"},
        workspace_id="kwarg",
    )
    assert captured["params"]["workspace_id"] == "explicit"


def test_enforce_workspace_filter_raises_without_workspace_param(tmp_path) -> None:
    """Cypher without $workspace_id reference is refused under enforcement."""
    from seocho.store.graph import WorkspaceFilterMissingError
    store = _build_ladybug_store(tmp_path)

    with pytest.raises(WorkspaceFilterMissingError) as ei:
        store.query(
            "MATCH (n:Person) RETURN n",  # no $workspace_id
            workspace_id="alpha",
            enforce_workspace_filter=True,
        )
    assert "MATCH (n:Person) RETURN n" in ei.value.cypher


def test_enforce_workspace_filter_passes_when_cypher_references_param(tmp_path) -> None:
    """Cypher that references $workspace_id is allowed under enforcement."""
    store = _build_ladybug_store(tmp_path)
    # Stub conn so we don't need a real graph
    class _Empty:
        column_names = []
        def __iter__(self): return iter([])

    store._conn.execute = lambda *a, **kw: _Empty()
    # Should not raise
    store.query(
        "MATCH (n:Person) WHERE n._workspace_id = $workspace_id RETURN n",
        workspace_id="alpha",
        enforce_workspace_filter=True,
    )


def test_default_back_compat_no_workspace_filter_runs_freely(tmp_path) -> None:
    """Default (no enforce, no workspace_id) preserves existing call-site contract."""
    store = _build_ladybug_store(tmp_path)
    class _Empty:
        column_names = []
        def __iter__(self): return iter([])
    store._conn.execute = lambda *a, **kw: _Empty()
    # No exception, no kwargs
    store.query("MATCH (n) RETURN n")


def test_execute_write_supports_same_kwargs(tmp_path) -> None:
    store = _build_ladybug_store(tmp_path)
    captured = {}

    def _capture(cypher, params=None):
        captured["params"] = params

    store._conn.execute = _capture
    store.execute_write(
        "MATCH (n) WHERE n._workspace_id = $workspace_id SET n.touched = true",
        workspace_id="alpha",
    )
    assert captured["params"]["workspace_id"] == "alpha"
