"""
Request correlation middleware.

Provides request ID tracking via ``X-Request-ID`` header and ContextVar
for thread-safe access throughout the request lifecycle.
"""

import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the current request ID (empty string outside request context)."""
    return _request_id_var.get()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Reads or generates ``X-Request-ID`` and attaches it to response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = _request_id_var.set(request_id)
        response: Response | None = None

        logger.info(
            "request_start request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )

        start = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.info(
                "request_end request_id=%s status=%s elapsed_ms=%.1f",
                request_id,
                getattr(response, "status_code", "?"),
                elapsed_ms,
            )
            _request_id_var.reset(token)

        if response is None:
            raise RuntimeError("Request pipeline returned no response")
        response.headers["X-Request-ID"] = request_id
        return response
