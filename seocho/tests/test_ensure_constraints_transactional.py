"""Regression tests for seocho-c2ck — transactional ontology schema migration.

When ``ensure_constraints(transactional=True)`` is used against a
Neo4jGraphStore, all DDL statements run in a single transaction. If any
statement fails, the entire migration rolls back so the database is
never left in a mixed-version state.

These tests use a stub Neo4j driver so we can verify the call shape
without needing a live Neo4j connection.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest


class _StubTx:
    def __init__(self, fail_on: int = -1) -> None:
        self._fail_on = fail_on
        self._call_count = 0
        self.committed = False
        self.rolled_back = False
        self.statements: List[str] = []

    def run(self, stmt: str, *a, **kw):
        self._call_count += 1
        self.statements.append(stmt)
        if self._call_count == self._fail_on:
            raise RuntimeError(f"simulated DDL failure on stmt #{self._call_count}")
        return None

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _StubSession:
    def __init__(self, tx: _StubTx) -> None:
        self._tx = tx

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def begin_transaction(self) -> _StubTx:
        return self._tx

    def run(self, stmt: str, *a, **kw):
        # Non-transactional path support
        return None


class _StubDriver:
    def __init__(self, tx: _StubTx) -> None:
        self._tx = tx

    def session(self, database: str = "neo4j") -> _StubSession:
        return _StubSession(self._tx)


def _make_minimal_ontology():
    from seocho import NodeDef, Ontology, P
    return Ontology(
        name="c2ck_test",
        nodes={"Person": NodeDef(properties={"name": P(str, unique=True)})},
    )


def _build_neo4j_store(driver):
    from seocho.store.graph import Neo4jGraphStore
    store = object.__new__(Neo4jGraphStore)
    store._driver = driver
    store._uri = "bolt://stub"
    store._user = "stub"
    store._schema_cache = {}
    store._schema_cache_ts = {}
    store._schema_cache_ttl = 60.0
    return store


def test_transactional_commits_on_success() -> None:
    """All statements run; tx.commit() called; success count == # of stmts."""
    tx = _StubTx()
    store = _build_neo4j_store(_StubDriver(tx))
    onto = _make_minimal_ontology()
    summary = store.ensure_constraints(onto, transactional=True)
    assert tx.committed is True
    assert tx.rolled_back is False
    assert summary["success"] == len(tx.statements)
    assert summary["errors"] == []


def test_transactional_rolls_back_on_mid_statement_failure() -> None:
    """Failing the first statement rolls back; success count is reset."""
    tx = _StubTx(fail_on=1)  # fail on first stmt — minimal ontology may emit just 1
    store = _build_neo4j_store(_StubDriver(tx))
    onto = _make_minimal_ontology()
    summary = store.ensure_constraints(onto, transactional=True)
    assert tx.committed is False
    assert tx.rolled_back is True
    assert summary["success"] == 0  # rollback undid everything
    assert len(summary["errors"]) == 1
    assert "rolled back" in summary["errors"][0]


def test_strict_plus_transactional_raises_on_rollback() -> None:
    """strict=True + transactional=True with a failure raises EnsureConstraintsError."""
    from seocho.store.graph import EnsureConstraintsError
    tx = _StubTx(fail_on=1)
    store = _build_neo4j_store(_StubDriver(tx))
    onto = _make_minimal_ontology()
    with pytest.raises(EnsureConstraintsError):
        store.ensure_constraints(onto, strict=True, transactional=True)
    assert tx.rolled_back is True


def test_default_non_transactional_preserves_back_compat() -> None:
    """Without transactional kwarg, the behaviour is unchanged (per-stmt session.run)."""
    tx = _StubTx()  # never used in non-transactional path
    store = _build_neo4j_store(_StubDriver(tx))
    onto = _make_minimal_ontology()
    summary = store.ensure_constraints(onto)
    # Non-transactional path goes through session.run, not tx.run, so:
    assert tx.committed is False
    assert tx.rolled_back is False
    assert summary["success"] >= 0
