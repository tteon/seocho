"""Regression tests for the Opik tracing backend (no live Opik).

Locks two fixes from 2026-05-30:
  1. Batching data-loss: log_span must pass end_time in the SINGLE client.trace()
     call and must NOT call trace.end() right after creation (SDK>=2.0 batches the
     create payload away → all-null traces). Asserts name/tags/metadata survive.
  2. SDK-version guard: an older Opik SDK major version logs a one-time warning.
  3. Init failure degrades silently (log_span no-ops, reason captured).

A fake ``opik`` module is injected into sys.modules so OpikBackend's
``import opik`` resolves to it.
"""
from __future__ import annotations

import sys

import pytest

import seocho.tracing as tracing
from seocho.tracing import OpikBackend


class _FakeTrace:
    def __init__(self):
        self.ended = False

    def end(self, *a, **k):
        self.ended = True


class _FakeClient:
    def __init__(self, **kw):
        self.trace_calls = []
        self.last_trace = None
        self.flushed = False

    def trace(self, **kw):
        self.trace_calls.append(kw)
        self.last_trace = _FakeTrace()
        return self.last_trace

    def flush(self):
        self.flushed = True


def _make_fake_opik(version="2.0.52", raise_on_init=False):
    import types
    mod = types.ModuleType("opik")
    mod.__version__ = version
    holder = {}

    class Opik:
        def __init__(self, **kw):
            if raise_on_init:
                raise RuntimeError("init boom")
            holder["client"] = _FakeClient(**kw)
            # expose so the test can read it back
            self._c = holder["client"]

        def __getattr__(self, name):
            return getattr(holder["client"], name)

    mod.Opik = Opik
    mod._holder = holder
    return mod


@pytest.fixture
def _opik_env(monkeypatch):
    for k in ("OPIK_URL_OVERRIDE", "OPIK_WORKSPACE", "OPIK_PROJECT_NAME", "OPIK_API_KEY"):
        monkeypatch.setenv(k, "")
    monkeypatch.setattr(tracing, "_OPIK_VERSION_WARNED", False)
    yield


def test_log_span_passes_end_time_single_call_no_end(_opik_env, monkeypatch):
    fake = _make_fake_opik()
    monkeypatch.setitem(sys.modules, "opik", fake)

    backend = OpikBackend(url="http://localhost:5173/api", workspace="default",
                          project_name="p")
    backend.log_span("my_trace", input_data={"q": "x"}, output_data={"a": "y"},
                     metadata={"slice": "S1"}, tags=["model:grok/grok-4.3", "retrieval:graph"])

    client = fake._holder["client"]
    assert len(client.trace_calls) == 1                # single create call
    kw = client.trace_calls[0]
    assert kw["name"] == "my_trace"                    # payload survives (not null)
    assert kw["tags"] == ["model:grok/grok-4.3", "retrieval:graph"]
    assert kw["metadata"] == {"slice": "S1"}
    assert "end_time" in kw and kw["end_time"] is not None
    assert client.last_trace.ended is False            # .end() never called


def test_version_guard_warns_on_old_sdk(_opik_env, monkeypatch, caplog):
    fake = _make_fake_opik(version="1.11.2")
    monkeypatch.setitem(sys.modules, "opik", fake)
    with caplog.at_level("WARNING"):
        OpikBackend(url="http://localhost:5173/api", project_name="p")
    assert any("Opik SDK version" in rec.message for rec in caplog.records)


def test_version_guard_quiet_on_current_sdk(_opik_env, monkeypatch, caplog):
    fake = _make_fake_opik(version="2.0.52")
    monkeypatch.setitem(sys.modules, "opik", fake)
    with caplog.at_level("WARNING"):
        OpikBackend(url="http://localhost:5173/api", project_name="p")
    assert not any("Opik SDK version" in rec.message for rec in caplog.records)


def test_init_failure_degrades_silently(_opik_env, monkeypatch):
    fake = _make_fake_opik(raise_on_init=True)
    monkeypatch.setitem(sys.modules, "opik", fake)
    backend = OpikBackend(url="http://localhost:5173/api", project_name="p")
    assert backend._client is None
    assert backend._init_error and "init boom" in backend._init_error
    # no-op, must not raise
    backend.log_span("t", tags=["x"], metadata={"k": "v"})


def test_init_does_not_mutate_os_environ(monkeypatch):
    # Regression #141: init must not write OPIK_* (incl. OPIK_API_KEY) into the
    # process environment, clobbering values the host app had set.
    import os

    fake = _make_fake_opik()
    monkeypatch.setitem(sys.modules, "opik", fake)
    monkeypatch.setattr(tracing, "_OPIK_VERSION_WARNED", False)
    original = {
        "OPIK_URL_OVERRIDE": "orig-url",
        "OPIK_WORKSPACE": "orig-ws",
        "OPIK_PROJECT_NAME": "orig-proj",
        "OPIK_API_KEY": "orig-key",
    }
    for k, v in original.items():
        monkeypatch.setenv(k, v)

    OpikBackend(url="http://h:1/api", workspace="ws-b",
                project_name="proj-b", api_key="key-b")

    for k, v in original.items():
        assert os.environ[k] == v  # unchanged


def test_config_passed_through_constructor(_opik_env, monkeypatch):
    # The Opik client is configured via its constructor instead of env vars.
    captured = {}
    fake = _make_fake_opik()
    base_opik = fake.Opik

    class CapturingOpik(base_opik):
        def __init__(self, **kw):
            captured.update(kw)
            super().__init__(**kw)

    fake.Opik = CapturingOpik
    monkeypatch.setitem(sys.modules, "opik", fake)

    OpikBackend(url="http://h:1/api", workspace="ws-b",
                project_name="proj-b", api_key="key-b")

    assert captured["project_name"] == "proj-b"
    assert captured["workspace"] == "ws-b"
    assert captured["host"] == "http://h:1/api"
    assert captured["api_key"] == "key-b"
