"""Tests for the runtime identity foundation (seocho-7fm).

Covers the token codec, mode resolution, and the PrincipalMiddleware wiring,
including the strangler-fig invariant: default mode 'none' is behaviour
preserving (anonymous, non-enforcing) and token mode rejects bad tokens with 401.
"""

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.identity import (
    ANONYMOUS,
    AuthError,
    Principal,
    PrincipalMiddleware,
    _b64e,
    get_principal,
    issue_token,
    resolve_principal,
    verify_token,
)

SECRET = "unit-test-secret"


# --------------------------------------------------------------------------- #
# Token codec
# --------------------------------------------------------------------------- #

def test_token_round_trip():
    token = issue_token(SECRET, subject="alice", role="user", workspace_id="acme")
    p = verify_token(token, SECRET)
    assert p == Principal(subject="alice", role="user", workspace_id="acme", authenticated=True)


def test_token_tamper_is_rejected():
    token = issue_token(SECRET, subject="alice", role="admin")
    payload_b64, sig = token.split(".")
    # flip the role claim but keep the original signature
    forged_payload = _b64e(
        json.dumps({"sub": "alice", "role": "admin", "ws": "other", "exp": 9999999999},
                   separators=(",", ":"), sort_keys=True).encode()
    )
    with pytest.raises(AuthError):
        verify_token(f"{forged_payload}.{sig}", SECRET)


def test_token_wrong_secret_is_rejected():
    token = issue_token(SECRET, subject="alice", role="user")
    with pytest.raises(AuthError, match="signature"):
        verify_token(token, "different-secret")


def test_token_expiry_is_enforced():
    token = issue_token(SECRET, subject="alice", role="user", ttl_seconds=10, now=1000.0)
    # valid just before expiry, invalid after
    assert verify_token(token, SECRET, now=1005.0).subject == "alice"
    with pytest.raises(AuthError, match="expired"):
        verify_token(token, SECRET, now=2000.0)


def test_token_unknown_role_is_rejected():
    # hand-sign a token whose role is not in VALID_ROLES
    payload_b64 = _b64e(
        json.dumps({"sub": "x", "role": "root", "ws": None, "exp": 9999999999},
                   separators=(",", ":"), sort_keys=True).encode()
    )
    sig = _b64e(hmac.new(SECRET.encode(), payload_b64.encode(), hashlib.sha256).digest())
    with pytest.raises(AuthError, match="invalid role"):
        verify_token(f"{payload_b64}.{sig}", SECRET)


def test_malformed_tokens_are_rejected():
    for bad in ["", "noseparator", "a.b.c", ".", "x."]:
        with pytest.raises(AuthError):
            verify_token(bad, SECRET)


def test_issue_token_rejects_bad_role():
    with pytest.raises(ValueError):
        issue_token(SECRET, subject="x", role="root")


# --------------------------------------------------------------------------- #
# Mode resolution
# --------------------------------------------------------------------------- #

def test_resolve_none_mode_is_anonymous():
    assert resolve_principal(None, mode="none") is ANONYMOUS
    assert resolve_principal("Bearer whatever", mode="none") is ANONYMOUS
    assert ANONYMOUS.authenticated is False


def test_resolve_token_mode_requires_secret():
    with pytest.raises(AuthError, match="SEOCHO_AUTH_SECRET"):
        resolve_principal("Bearer x", mode="token", secret="")


def test_resolve_token_mode_requires_bearer_header():
    with pytest.raises(AuthError, match="missing bearer token"):
        resolve_principal(None, mode="token", secret=SECRET)
    with pytest.raises(AuthError, match="missing bearer token"):
        resolve_principal("Basic abc", mode="token", secret=SECRET)


def test_resolve_token_mode_valid():
    token = issue_token(SECRET, subject="bob", role="viewer", workspace_id="ws1")
    p = resolve_principal(f"Bearer {token}", mode="token", secret=SECRET)
    assert p.subject == "bob" and p.role == "viewer" and p.workspace_id == "ws1"
    assert p.authenticated is True


def test_resolve_unknown_mode():
    with pytest.raises(AuthError, match="unknown"):
        resolve_principal(None, mode="weird")


# --------------------------------------------------------------------------- #
# Middleware integration (TestClient, offline)
# --------------------------------------------------------------------------- #

def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(PrincipalMiddleware)

    @app.get("/whoami")
    def whoami():
        p = get_principal()
        return {"subject": p.subject, "role": p.role,
                "workspace_id": p.workspace_id, "authenticated": p.authenticated}

    return app


def test_middleware_none_mode_is_anonymous(monkeypatch):
    monkeypatch.setenv("SEOCHO_AUTH_MODE", "none")
    client = TestClient(_app())
    r = client.get("/whoami")
    assert r.status_code == 200
    assert r.json() == {"subject": "anonymous", "role": "user",
                        "workspace_id": None, "authenticated": False}


def test_middleware_token_mode_rejects_missing_and_bad(monkeypatch):
    monkeypatch.setenv("SEOCHO_AUTH_MODE", "token")
    monkeypatch.setenv("SEOCHO_AUTH_SECRET", SECRET)
    client = TestClient(_app())
    assert client.get("/whoami").status_code == 401
    assert client.get("/whoami", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_middleware_token_mode_accepts_valid(monkeypatch):
    monkeypatch.setenv("SEOCHO_AUTH_MODE", "token")
    monkeypatch.setenv("SEOCHO_AUTH_SECRET", SECRET)
    token = issue_token(SECRET, subject="carol", role="admin", workspace_id="tenant-7")
    client = TestClient(_app())
    r = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "carol" and body["role"] == "admin"
    assert body["workspace_id"] == "tenant-7" and body["authenticated"] is True
