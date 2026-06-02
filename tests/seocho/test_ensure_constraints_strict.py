"""Regression tests for seocho-hvoe — ensure_constraints strict mode.

ensure_constraints used to swallow per-statement failures into
``summary['errors']`` and return a success-shaped dict. Callers who didn't
inspect the array proceeded against a partially-applied schema. The fix
adds a ``strict=True`` opt-in that raises ``EnsureConstraintsError`` on
any errors, preserving back-compat for callers that pass nothing.
"""

from __future__ import annotations

import tempfile

import pytest


def _make_minimal_ontology():
    from seocho import NodeDef, Ontology, P
    return Ontology(
        name="hvoe_test",
        nodes={"Person": NodeDef(properties={"name": P(str, unique=True)})},
    )


def _build_ladybug_store(tmp_path):
    """Real LadybugGraphStore against a temp file — covers the embedded path."""
    from seocho.store.graph import LadybugGraphStore
    return LadybugGraphStore(f"{tmp_path}/hvoe.lbug")


def test_default_returns_summary_back_compat(tmp_path) -> None:
    """No strict kwarg → returns summary even when there are errors."""
    store = _build_ladybug_store(tmp_path)
    onto = _make_minimal_ontology()
    summary = store.ensure_constraints(onto)
    assert "success" in summary
    assert "errors" in summary


def test_strict_does_not_raise_on_clean_run(tmp_path) -> None:
    """strict=True without any errors returns the same summary."""
    store = _build_ladybug_store(tmp_path)
    onto = _make_minimal_ontology()
    summary = store.ensure_constraints(onto, strict=True)
    assert summary["errors"] == []
    assert summary["success"] >= 1


def test_strict_raises_when_errors_present(tmp_path) -> None:
    """When the underlying call appends to errors, strict=True raises."""
    from seocho.store.graph import EnsureConstraintsError
    store = _build_ladybug_store(tmp_path)
    onto = _make_minimal_ontology()

    # Force errors by replacing _conn.execute to always raise.
    class _BrokenConn:
        def execute(self, *a, **kw):
            raise RuntimeError("simulated DDL failure")

    store._conn = _BrokenConn()

    with pytest.raises(EnsureConstraintsError) as ei:
        store.ensure_constraints(onto, strict=True)

    err = ei.value
    assert err.errors, "Expected errors to be preserved on the exception"
    assert "simulated DDL failure" in err.errors[0]
    assert err.summary["success"] == 0


def test_non_strict_returns_summary_with_errors(tmp_path) -> None:
    """Default (non-strict) preserves the original silent-summary behaviour."""
    store = _build_ladybug_store(tmp_path)
    onto = _make_minimal_ontology()

    class _BrokenConn:
        def execute(self, *a, **kw):
            raise RuntimeError("simulated DDL failure")

    store._conn = _BrokenConn()

    summary = store.ensure_constraints(onto)  # no strict kwarg
    assert summary["errors"], "Errors should be reported in the summary"
    assert summary["success"] == 0


def test_ensure_constraints_error_exposes_summary() -> None:
    """The exception preserves the original summary dict for caller inspection."""
    from seocho.store.graph import EnsureConstraintsError
    summary = {"success": 0, "errors": ["a: x", "b: y"]}
    err = EnsureConstraintsError(summary)
    assert err.summary is summary
    assert err.errors == ["a: x", "b: y"]
    assert "2 statement" in str(err)
