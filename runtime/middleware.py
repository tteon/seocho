"""
Request correlation and boundary-metrics middleware.

Provides request ID tracking via ``X-Request-ID`` header and ContextVar
for thread-safe access throughout the request lifecycle, plus the golden-signal
instrumentation (count, duration, in-flight) for every HTTP route.
"""

import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from seocho.metrics import get_metrics

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


def _route_template(request: Request) -> str:
    """Bounded operation label: the matched route template, never the raw path.

    The router fills ``scope["route"]`` while handling the request, so after
    ``call_next`` the template (``/semantic/artifacts/{artifact_id}``) is
    available. Unmatched paths collapse into one label — raw 404 paths are
    attacker-controlled and would blow up metric cardinality.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """Emit count/duration/in-flight for every HTTP request.

    Uses the ``seocho.agent.request.*`` family from the ADR-0146 catalog with
    ``operation=<route template>``. Exceptions that FastAPI exception handlers
    convert to responses surface here as their status code; anything that
    still propagates is recorded as ``server_error`` and re-raised.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        metrics = get_metrics()
        start = time.perf_counter()
        # In-flight is attributed to the raw-safe fallback first and corrected
        # after routing; a fixed label keeps the up/down pairing consistent.
        metrics.add("seocho.agent.request.inflight", 1, {"operation": "http"})
        # Vocabulary matches the existing seocho.agent.request emitters ("ok"/
        # "error"/"timeout") so the seocho:agent_error_ratio:5m recording rule
        # (outcome=~"error|timeout") counts HTTP failures without a rule change.
        # 4xx is deliberately its own label: client mistakes must not burn the
        # server error budget.
        outcome = "error"
        error_type = ""
        try:
            response = await call_next(request)
            status = response.status_code
            if status >= 500:
                outcome = "error"
            elif status >= 400:
                outcome = "client_error"
            else:
                outcome = "ok"
            return response
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            elapsed = time.perf_counter() - start
            operation = _route_template(request)
            metrics.add("seocho.agent.request.inflight", -1, {"operation": "http"})
            metrics.add(
                "seocho.agent.request.count",
                attributes={"operation": operation, "outcome": outcome},
            )
            duration_attrs = {"operation": operation, "outcome": outcome}
            if error_type:
                duration_attrs["error.type"] = error_type
            metrics.record("seocho.agent.request.duration", elapsed, duration_attrs)
