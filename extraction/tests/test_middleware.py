"""Tests for request ID middleware."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
def client(test_app):
    return TestClient(test_app)


class TestRequestIDMiddleware:
    def test_generates_request_id(self, client):
        response = client.get("/test")
        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
        # Should be a UUID-like string
        rid = response.headers["X-Request-ID"]
        assert len(rid) > 0

    def test_preserves_provided_request_id(self, client):
        custom_id = "my-custom-request-123"
        response = client.get("/test", headers={"X-Request-ID": custom_id})
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == custom_id

    def test_request_id_available_in_handler(self, client):
        custom_id = "handler-test-456"
        response = client.get("/test", headers={"X-Request-ID": custom_id})
        data = response.json()
        assert data["request_id"] == custom_id

    def test_request_id_empty_outside_context(self):
        assert get_request_id() == ""
