"""Tests for structured error responses."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from exceptions import (
    SeochoError,
    ConfigurationError,
    InfrastructureError,
    DataValidationError,
    PipelineError,
    MissingAPIKeyError,
    OpenAIAPIError,
    InvalidLabelError,
    ExtractionError,
)
from middleware import RequestIDMiddleware, get_request_id


@pytest.fixture
def error_app():
    """FastAPI app with SeochoError exception handler for testing."""
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    _EXCEPTION_STATUS_MAP = {
        ConfigurationError: 400,
        DataValidationError: 422,
        PipelineError: 422,
        InfrastructureError: 502,
    }

    @app.exception_handler(SeochoError)
    async def seocho_error_handler(request: Request, exc: SeochoError):
        status_code = 500
        for exc_type, code in _EXCEPTION_STATUS_MAP.items():
            if isinstance(exc, exc_type):
                status_code = code
                break

        request_id = get_request_id()
        body = {
            "error": {
                "error_code": type(exc).__name__,
                "message": str(exc),
                "request_id": request_id,
            }
        }
        return JSONResponse(status_code=status_code, content=body)

    @app.get("/raise/{exc_type}")
    async def raise_exception(exc_type: str):
        exc_map = {
            "config": MissingAPIKeyError("API key missing"),
            "infra": OpenAIAPIError("rate limited"),
            "validation": InvalidLabelError("bad label"),
            "pipeline": ExtractionError("JSON parse failed"),
            "base": SeochoError("generic error"),
        }
        raise exc_map[exc_type]

    return app


@pytest.fixture
async def client(error_app):
    transport = httpx.ASGITransport(app=error_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


@pytest.mark.anyio
class TestStructuredErrorResponses:
    async def test_configuration_error_returns_400(self, client):
        response = await client.get("/raise/config")
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["error_code"] == "MissingAPIKeyError"
        assert "API key" in data["error"]["message"]

    async def test_infrastructure_error_returns_502(self, client):
        response = await client.get("/raise/infra")
        assert response.status_code == 502
        data = response.json()
        assert data["error"]["error_code"] == "OpenAIAPIError"

    async def test_validation_error_returns_422(self, client):
        response = await client.get("/raise/validation")
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["error_code"] == "InvalidLabelError"

    async def test_pipeline_error_returns_422(self, client):
        response = await client.get("/raise/pipeline")
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["error_code"] == "ExtractionError"

    async def test_base_seocho_error_returns_500(self, client):
        response = await client.get("/raise/base")
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["error_code"] == "SeochoError"

    async def test_request_id_in_error_body(self, client):
        custom_id = "err-req-789"
        response = await client.get(
            "/raise/config",
            headers={"X-Request-ID": custom_id},
        )
        data = response.json()
        assert data["error"]["request_id"] == custom_id

    async def test_x_request_id_in_error_response_headers(self, client):
        response = await client.get("/raise/config")
        assert "X-Request-ID" in response.headers
