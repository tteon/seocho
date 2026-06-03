"""Tests for the local fastembed (bge) embedding backend (ADR-0103 S9).

The FastEmbedBackend implements EmbeddingBackend.embed with a fake model (no
download); a live test runs only when fastembed is importable. The point: the
vector / few-shot index can use a local bge backend instead of OpenAI.
"""

from __future__ import annotations

import importlib.util

import pytest

from seocho.store.fastembed_backend import FastEmbedBackend, make_fastembed_backend
from seocho.store.llm import EmbeddingBackend


class _FakeModel:
    def embed(self, texts):
        # deterministic 3-dim vector per text (len, first-char, last-char)
        for t in texts:
            yield [float(len(t)), float(ord(t[0]) if t else 0), float(ord(t[-1]) if t else 0)]


def test_fastembed_backend_implements_interface_and_embeds():
    be = FastEmbedBackend(_FakeModel())
    assert isinstance(be, EmbeddingBackend)
    vecs = be.embed(["abc", "hello"])
    assert vecs == [[3.0, 97.0, 99.0], [5.0, 104.0, 111.0]]
    assert be.embed([]) == []


def test_fastembed_backend_ignores_model_kwarg():
    be = FastEmbedBackend(_FakeModel())
    assert be.embed(["x"], model="anything") == [[1.0, 120.0, 120.0]]


def test_make_fastembed_backend_returns_none_without_package(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name == "fastembed" or name.startswith("fastembed."):
            raise ImportError("blocked")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert make_fastembed_backend() is None


@pytest.mark.skipif(importlib.util.find_spec("fastembed") is None,
                    reason="fastembed not installed")
def test_make_fastembed_backend_live_embeds_consistent_dims():
    be = make_fastembed_backend()
    assert be is not None
    vecs = be.embed(["total revenue", "net income"])
    assert len(vecs) == 2
    assert len(vecs[0]) == len(vecs[1]) > 0          # consistent embedding dim
    assert all(isinstance(x, float) for x in vecs[0])
