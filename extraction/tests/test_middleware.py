"""Tests for request ID middleware."""

import os
import sys

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from middleware import RequestIDMiddleware, get_request_id


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
