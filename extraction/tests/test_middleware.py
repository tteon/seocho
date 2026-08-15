"""Tests for request ID middleware."""

import os
import sys

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from runtime.middleware import RequestIDMiddleware, get_request_id


@pytest.fixture
def test_app():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/test")
    async def test_endpoint():
        return {"request_id": get_request_id()}

    return app


@pytest.fixture
async def async_client(test_app):
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.anyio
class TestRequestIDMiddleware:
    async def test_generates_request_id(self, async_client):
        response = await async_client.get("/test")
        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
        rid = response.headers["X-Request-ID"]
        assert len(rid) > 0

    async def test_preserves_provided_request_id(self, async_client):
        custom_id = "my-custom-request-123"
        response = await async_client.get("/test", headers={"X-Request-ID": custom_id})
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == custom_id

    async def test_request_id_available_in_handler(self, async_client):
        custom_id = "handler-test-456"
        response = await async_client.get("/test", headers={"X-Request-ID": custom_id})
        data = response.json()
        assert data["request_id"] == custom_id

    async def test_request_id_empty_outside_context(self, async_client):
        del async_client
        assert get_request_id() == ""


class _Instrument:
    def __init__(self):
        self.calls = []

    def add(self, value, attributes=None):
        self.calls.append(("add", value, attributes))

    def record(self, value, attributes=None):
        self.calls.append(("record", value, attributes))

    def set(self, value, attributes=None):
        self.calls.append(("set", value, attributes))


class _Meter:
    def __init__(self):
        self.instruments = {}

    def _make(self, name, **kwargs):
        self.instruments[name] = _Instrument()
        return self.instruments[name]

    create_counter = _make
    create_up_down_counter = _make
    create_gauge = _make
    create_histogram = _make


@pytest.fixture
def captured_metrics(monkeypatch):
    import seocho.metrics as metrics_module

    meter = _Meter()
    monkeypatch.setattr(
        metrics_module, "_metrics", metrics_module.ProductionMetrics(meter)
    )
    return meter


@pytest.fixture
def metrics_app():
    from runtime.middleware import RequestMetricsMiddleware

    app = FastAPI()
    app.add_middleware(RequestMetricsMiddleware)

    @app.get("/things/{thing_id}")
    async def get_thing(thing_id: str):
        if thing_id == "boom":
            raise ValueError("boom")
        if thing_id == "missing":
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="missing")
        return {"thing_id": thing_id}

    return app


@pytest.fixture
async def metrics_client(metrics_app):
    transport = httpx.ASGITransport(app=metrics_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client


@pytest.mark.anyio
class TestRequestMetricsMiddleware:
    async def test_success_counts_route_template_not_raw_path(
        self, metrics_client, captured_metrics
    ):
        response = await metrics_client.get("/things/abc-123")
        assert response.status_code == 200
        count = captured_metrics.instruments["seocho.agent.request.count"]
        assert count.calls == [
            ("add", 1, {"operation": "/things/{thing_id}", "outcome": "ok"})
        ]
        duration = captured_metrics.instruments["seocho.agent.request.duration"]
        assert len(duration.calls) == 1
        _, elapsed, attrs = duration.calls[0]
        assert elapsed >= 0
        assert attrs == {"operation": "/things/{thing_id}", "outcome": "ok"}

    async def test_client_error_is_not_a_server_error(
        self, metrics_client, captured_metrics
    ):
        response = await metrics_client.get("/things/missing")
        assert response.status_code == 404
        count = captured_metrics.instruments["seocho.agent.request.count"]
        assert count.calls[0][2]["outcome"] == "client_error"

    async def test_unhandled_exception_counts_as_error_and_reraises(
        self, metrics_client, captured_metrics
    ):
        with pytest.raises(ValueError):
            await metrics_client.get("/things/boom")
        count = captured_metrics.instruments["seocho.agent.request.count"]
        assert count.calls[0][2]["outcome"] == "error"
        duration = captured_metrics.instruments["seocho.agent.request.duration"]
        assert duration.calls[0][2]["error.type"] == "ValueError"

    async def test_inflight_returns_to_zero(self, metrics_client, captured_metrics):
        await metrics_client.get("/things/abc")
        inflight = captured_metrics.instruments["seocho.agent.request.inflight"]
        deltas = [call[1] for call in inflight.calls]
        assert deltas == [1, -1]
