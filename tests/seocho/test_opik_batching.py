"""Regression for #119 — OpikBackend must construct its client with
batching=False. Opik's default async batching drops short-lived burst traces
(each created and immediately ended) before they flush, so only ~1 of N
persisted.
"""

from __future__ import annotations

import sys
import types

import pytest

import seocho.tracing as tracing
from seocho.tracing import OpikBackend


def _fake_opik(captured):
    mod = types.ModuleType("opik")
    mod.__version__ = "2.0.52"

    class Opik:
        def __init__(self, **kw):
            captured.update(kw)

        def __getattr__(self, name):
            return lambda *a, **k: None

    mod.Opik = Opik
    return mod


@pytest.fixture(autouse=True)
def _quiet_version(monkeypatch):
    monkeypatch.setattr(tracing, "_OPIK_VERSION_WARNED", False)
    for k in ("OPIK_URL_OVERRIDE", "OPIK_WORKSPACE", "OPIK_PROJECT_NAME", "OPIK_API_KEY"):
        monkeypatch.setenv(k, "")


def test_client_constructed_with_batching_disabled(monkeypatch):
    captured: dict = {}
    monkeypatch.setitem(sys.modules, "opik", _fake_opik(captured))

    OpikBackend(url="http://localhost:5173/api", project_name="p")

    assert captured.get("batching") is False
