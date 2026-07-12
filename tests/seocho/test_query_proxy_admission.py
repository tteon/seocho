from __future__ import annotations

import threading

import pytest

from seocho.query.query_proxy import (
    QueryAdmissionController,
    QueryAdmissionRejected,
    QueryProxy,
    QueryRequest,
)


class _BlockingStore:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def query(self, cypher, *, params=None, database="neo4j"):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=2)
        return [{"ok": True}]


def _request() -> QueryRequest:
    return QueryRequest(cypher="MATCH (n) RETURN n", workspace_id="acme")


def test_admission_rejects_excess_query_without_hitting_backend() -> None:
    store = _BlockingStore()
    controller = QueryAdmissionController(max_inflight=1, wait_seconds=0)
    proxy = QueryProxy(store, admission_controller=controller)
    completed: list[list[dict]] = []
    worker = threading.Thread(target=lambda: completed.append(proxy.query(_request())))
    worker.start()
    assert store.entered.wait(timeout=1)

    with pytest.raises(QueryAdmissionRejected, match="capacity exhausted"):
        proxy.query(_request())
    assert store.calls == 1

    store.release.set()
    worker.join(timeout=2)
    assert completed == [[{"ok": True}]]


def test_zero_limit_preserves_unbounded_default() -> None:
    controller = QueryAdmissionController(max_inflight=0)
    assert controller.acquire() is True
    controller.release()


def test_admission_defaults_are_read_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("SEOCHO_GRAPH_QUERY_MAX_INFLIGHT", "8")
    monkeypatch.setenv("SEOCHO_GRAPH_QUERY_ADMISSION_WAIT_SECONDS", "0.05")
    proxy = QueryProxy(_BlockingStore())
    assert proxy._admission.max_inflight == 8
    assert proxy._admission.wait_seconds == 0.05


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("SEOCHO_GRAPH_QUERY_MAX_INFLIGHT", "-1"),
        ("SEOCHO_GRAPH_QUERY_ADMISSION_WAIT_SECONDS", "-0.1"),
    ),
)
def test_invalid_admission_environment_fails_closed(monkeypatch, name, value) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="must be non-negative"):
        QueryProxy(_BlockingStore())
