"""Runtime identity and pluggable authentication.

A strangler-fig seam that puts an authenticated :class:`Principal` in front of
the runtime without rewriting every endpoint. The flow is:

    request --> PrincipalMiddleware.resolve_principal() --> ContextVar
            --> policy.require_runtime_permission() reads the principal

Modes (env ``SEOCHO_AUTH_MODE``):

* ``none`` (default) — every request gets the anonymous, unconstrained
  principal. This preserves the pre-auth behavior exactly: the policy engine
  treats unauthenticated principals as non-enforcing, so nothing is gated. This
  is the safe default for local/dev and keeps existing callers working.
* ``token`` — an ``Authorization: Bearer <token>`` header is required and
  validated as an HMAC-signed compact token (see :func:`issue_token`) using
  ``SEOCHO_AUTH_SECRET``. The validated claims (subject, role, workspace)
  populate the principal, and the policy engine enforces against it.

OIDC / JWKS validation can be added later behind :func:`resolve_principal`
without changing any caller: it just needs to return a :class:`Principal`.
The token format here is intentionally dependency-free (stdlib hmac/hashlib)
so the foundation ships without pulling a JWT library; it is not a drop-in JWT.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

VALID_ROLES = ("admin", "user", "viewer")


class AuthError(Exception):
    """Raised when a request fails authentication (surfaced as HTTP 401)."""


@dataclass(frozen=True)
class Principal:
    """Who is making a request.

    ``workspace_id`` is the workspace the principal is scoped to; ``None`` means
    unconstrained (any workspace). ``authenticated`` is True only when a token
    was validated — the policy engine uses it to decide whether to enforce.
    """

    subject: str
    role: str
    workspace_id: Optional[str]
    authenticated: bool


# Anonymous principal used when auth is disabled. authenticated=False signals the
# policy engine NOT to enforce, which reproduces the pre-auth behavior.
ANONYMOUS = Principal(
    subject="anonymous", role="user", workspace_id=None, authenticated=False
)


# --------------------------------------------------------------------------- #
# Token codec (stdlib HMAC — payload_b64.signature_b64)
# --------------------------------------------------------------------------- #

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(
    secret: str,
    *,
    subject: str,
    role: str,
    workspace_id: Optional[str] = None,
    ttl_seconds: int = 3600,
    now: Optional[float] = None,
) -> str:
    """Issue an HMAC-signed compact token. Used by token issuers and tests."""
    if role not in VALID_ROLES:
        raise ValueError(f"invalid role: {role!r} (expected one of {VALID_ROLES})")
    issued = now if now is not None else time.time()
    payload = {
        "sub": subject,
        "role": role,
        "ws": workspace_id,
        "exp": int(issued + ttl_seconds),
    }
    payload_b64 = _b64e(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    sig = _b64e(hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest())
    return f"{payload_b64}.{sig}"


def verify_token(token: str, secret: str, *, now: Optional[float] = None) -> Principal:
    """Verify an HMAC-signed token and return the :class:`Principal`.

    Raises :class:`AuthError` on any malformed/tampered/expired token.
    """
    parts = token.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise AuthError("malformed token")
    payload_b64, sig = parts
    expected = _b64e(
        hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    )
    # constant-time comparison — never short-circuit on signature mismatch
    if not hmac.compare_digest(sig, expected):
        raise AuthError("bad token signature")
    try:
        payload = json.loads(_b64d(payload_b64))
    except Exception as exc:  # noqa: BLE001 — any decode failure is an auth failure
        raise AuthError("malformed token payload") from exc
    exp = payload.get("exp")
    current = now if now is not None else time.time()
    if exp is not None and current > exp:
        raise AuthError("token expired")
    role = payload.get("role")
    if role not in VALID_ROLES:
        raise AuthError(f"invalid role in token: {role!r}")
    subject = payload.get("sub")
    if not subject:
        raise AuthError("token missing subject")
    return Principal(
        subject=str(subject),
        role=role,
        workspace_id=payload.get("ws"),
        authenticated=True,
    )


# --------------------------------------------------------------------------- #
# Mode resolution
# --------------------------------------------------------------------------- #

def auth_mode() -> str:
    return (os.getenv("SEOCHO_AUTH_MODE", "none").strip().lower() or "none")


def resolve_principal(
    authorization: Optional[str],
    *,
    mode: Optional[str] = None,
    secret: Optional[str] = None,
    now: Optional[float] = None,
) -> Principal:
    """Resolve the principal for a request from its Authorization header.

    ``none`` -> anonymous. ``token`` -> validated bearer token. Raises
    :class:`AuthError` (HTTP 401) when token mode is on and the token is
    missing/invalid, or when token mode is misconfigured (no secret).
    """
    mode = (mode or auth_mode())
    if mode == "none":
        return ANONYMOUS
    if mode == "token":
        secret = secret if secret is not None else os.getenv("SEOCHO_AUTH_SECRET", "")
        if not secret:
            raise AuthError("SEOCHO_AUTH_MODE=token but SEOCHO_AUTH_SECRET is unset")
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthError("missing bearer token")
        return verify_token(authorization[len("Bearer "):].strip(), secret, now=now)
    raise AuthError(f"unknown SEOCHO_AUTH_MODE: {mode!r}")


# --------------------------------------------------------------------------- #
# Per-request principal (ContextVar — mirrors RequestIDMiddleware)
# --------------------------------------------------------------------------- #

_principal_var: ContextVar[Principal] = ContextVar("seocho_principal", default=ANONYMOUS)


def get_principal() -> Principal:
    """Return the principal for the current request (anonymous outside one)."""
    return _principal_var.get()


class PrincipalMiddleware(BaseHTTPMiddleware):
    """Resolve the principal once per request and bind it to the ContextVar.

    On token mode, an invalid/missing token short-circuits to HTTP 401 before
    the endpoint runs. On ``none`` mode this is a near no-op (anonymous).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            principal = resolve_principal(request.headers.get("Authorization"))
        except AuthError as exc:
            return JSONResponse(
                status_code=401,
                content={"error": {"error_code": "AuthError", "message": str(exc)}},
            )
        token = _principal_var.set(principal)
        request.state.principal = principal
        try:
            return await call_next(request)
        finally:
            _principal_var.reset(token)
