"""Regression tests for seocho-sdtq — LadybugGraphStore concurrent-write safety.

Background: Ladybug's ``Connection`` is not safe for concurrent use.
Two threads writing to the same LadybugGraphStore instance could
interleave statements or corrupt internal state. The fix wraps every
``self._conn.execute(...)`` call in an ``RLock`` via the
``_locked_execute()`` method.

These tests verify:
1. ``_conn_lock`` exists and is an RLock.
2. ``_locked_execute`` acquires the lock during the underlying execute.
3. Sequential writes and parallel writes produce the same final state.
"""

from __future__ import annotations

import threading
import time

import pytest


def _make_minimal_ontology():
    from seocho import NodeDef, Ontology, P
    return Ontology(
        name="sdtq_test",
        nodes={
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
        },
    )


def test_lock_attribute_exists(tmp_path) -> None:
    from seocho.store.graph import LadybugGraphStore
    store = LadybugGraphStore(f"{tmp_path}/sdtq.lbug")
    assert hasattr(store, "_conn_lock")
    # RLock is reentrant — confirm we can acquire twice from the same thread
    with store._conn_lock:
        with store._conn_lock:
            pass


def test_locked_execute_holds_lock_during_call(tmp_path) -> None:
    """During the underlying execute, a *different thread* cannot acquire the lock."""
    from seocho.store.graph import LadybugGraphStore
    store = LadybugGraphStore(f"{tmp_path}/sdtq2.lbug")

    held_blocked = {"value": False}
    in_execute = threading.Event()
    finish_execute = threading.Event()

    def _slow_execute(*a, **kw):
        in_execute.set()
        finish_execute.wait(timeout=2)
        class _Empty:
            column_names = []
            def __iter__(self): return iter([])
        return _Empty()

    store._conn.execute = _slow_execute

    def _caller():
        store._locked_execute("MATCH (n) RETURN n")

    t_caller = threading.Thread(target=_caller)
    t_caller.start()
    in_execute.wait(timeout=2)
    # Now the caller is inside _slow_execute, holding the lock.
    # A different thread must not be able to acquire it.
    held_blocked["value"] = not store._conn_lock.acquire(blocking=False)
    finish_execute.set()
    t_caller.join(timeout=2)
    assert held_blocked["value"] is True, (
        "Lock was not held during _conn.execute — concurrent writes can race"
    )


def test_concurrent_writes_serialize_correctly(tmp_path) -> None:
    """Two threads writing concurrently produce the union of nodes, not corruption."""
    from seocho.store.graph import LadybugGraphStore
    store = LadybugGraphStore(f"{tmp_path}/sdtq3.lbug")
    onto = _make_minimal_ontology()
    store.ensure_constraints(onto)

    def _write_batch(start: int, count: int):
        nodes = [
            {"id": f"p{i}", "label": "Person", "properties": {"name": f"Person{i}"}}
            for i in range(start, start + count)
        ]
        store.write(nodes, [], workspace_id="test", source_id=f"src-{start}")

    t1 = threading.Thread(target=_write_batch, args=(0, 10))
    t2 = threading.Thread(target=_write_batch, args=(10, 10))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # Both batches should have been written successfully — count rows directly.
    rows = store.query("MATCH (p:Person) RETURN count(p) AS cnt")
    assert rows, "Expected at least one row from count() query"
    cnt = rows[0].get("cnt") or rows[0].get("col_0") or 0
    assert cnt == 20, (
        f"Concurrent writes lost rows: expected 20, got {cnt}. "
        f"Indicates the lock didn't serialise correctly."
    )
