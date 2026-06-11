"""Tests for the access-decision audit log (seocho-bk0)."""

import json

import pytest

from runtime.audit import build_event, record_access
from runtime.identity import ANONYMOUS, Principal
from runtime.policy import require_runtime_permission

USER = Principal(subject="alice", role="user", workspace_id="acme", authenticated=True)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_build_event_allow_and_deny():
    allow = build_event(
        principal=USER, action="run_agent", workspace_id="acme",
        allowed=True, reason="ok", request_id="req-1", ts=1234.0,
    )
    assert allow == {
        "ts": 1234.0, "request_id": "req-1", "subject": "alice", "role": "user",
        "authenticated": True, "workspace_id": "acme", "action": "run_agent",
        "decision": "allow", "reason": "ok",
    }
    deny = build_event(
        principal=USER, action="run_agent", workspace_id="other",
        allowed=False, reason="cross-tenant", request_id="", ts=1.0,
    )
    assert deny["decision"] == "deny" and deny["reason"] == "cross-tenant"


def test_record_access_noop_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("SEOCHO_AUDIT_LOG_PATH", raising=False)
    target = tmp_path / "audit.jsonl"
    record_access(action="run_agent", workspace_id="acme", allowed=True, principal=USER)
    assert not target.exists()  # default is no-op


def test_record_access_writes_and_appends(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    record_access(action="run_agent", workspace_id="acme", allowed=True,
                  reason="ok", principal=USER, request_id="r1", path=path, now=1.0)
    record_access(action="run_debate", workspace_id="other", allowed=False,
                  reason="denied", principal=USER, request_id="r2", path=path, now=2.0)
    events = _read(path)
    assert len(events) == 2
    assert events[0]["decision"] == "allow" and events[0]["action"] == "run_agent"
    assert events[1]["decision"] == "deny" and events[1]["request_id"] == "r2"
    assert events[1]["subject"] == "alice"


def test_record_access_write_failure_does_not_raise():
    # An unwritable path must be swallowed (auditing can't take down requests).
    record_access(action="x", workspace_id="acme", allowed=True,
                  principal=USER, path="/this/dir/does/not/exist/audit.jsonl")


def test_require_permission_audit_integration(tmp_path, monkeypatch):
    path = str(tmp_path / "audit.jsonl")
    monkeypatch.setenv("SEOCHO_AUDIT_LOG_PATH", path)
    viewer = Principal(subject="v", role="viewer", workspace_id=None, authenticated=True)
    user = Principal(subject="u", role="user", workspace_id=None, authenticated=True)

    # allowed action -> recorded as allow, no raise
    require_runtime_permission(action="read_databases", workspace_id="acme", principal=viewer)
    # denied action -> recorded as deny, then raises
    with pytest.raises(PermissionError):
        require_runtime_permission(action="run_debate", workspace_id="acme", principal=viewer)
    # allowed for a normal user too
    require_runtime_permission(action="run_agent", workspace_id="acme", principal=user)

    events = _read(path)
    decisions = [(e["subject"], e["action"], e["decision"]) for e in events]
    assert ("v", "read_databases", "allow") in decisions
    assert ("v", "run_debate", "deny") in decisions
    assert ("u", "run_agent", "allow") in decisions
