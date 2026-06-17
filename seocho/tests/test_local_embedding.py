from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from seocho.store.local_embedding import (
    LOCAL_BGE_DEVICE_ENV,
    LocalBGEEmbeddingBackend,
    resolve_local_embedding_device,
)


class _FakeSentenceTransformer:
    calls: list[dict[str, object]] = []

    def __init__(self, model: str, *, device: str | None = None) -> None:
        self.model = model
        self.device = device
        self.calls.append({"model": model, "device": device})

    def get_sentence_embedding_dimension(self) -> int:
        return 3

    def encode(self, texts, **kwargs):
        return [
            SimpleNamespace(tolist=lambda text=text: [float(len(text)), 0.0, 1.0])
            for text in texts
        ]


def _install_fake_sentence_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSentenceTransformer.calls = []
    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = _FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)


def test_resolve_auto_prefers_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOCAL_BGE_DEVICE_ENV, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)),
    )

    assert resolve_local_embedding_device() == "cuda"


def test_resolve_auto_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOCAL_BGE_DEVICE_ENV, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
    )

    assert resolve_local_embedding_device() == "cpu"


def test_env_device_is_used_by_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sentence_transformers(monkeypatch)
    monkeypatch.setenv(LOCAL_BGE_DEVICE_ENV, "cpu")

    backend = LocalBGEEmbeddingBackend(model="BAAI/bge-small-en-v1.5")

    assert backend.device == "cpu"
    assert backend.requested_device == "cpu"
    assert _FakeSentenceTransformer.calls == [
        {"model": "BAAI/bge-small-en-v1.5", "device": "cpu"}
    ]


def test_explicit_device_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sentence_transformers(monkeypatch)
    monkeypatch.setenv(LOCAL_BGE_DEVICE_ENV, "cpu")

    backend = LocalBGEEmbeddingBackend(model="BAAI/bge-small-en-v1.5", device="cuda")

    assert backend.device == "cuda"
    assert backend.requested_device == "cuda"
    assert _FakeSentenceTransformer.calls == [
        {"model": "BAAI/bge-small-en-v1.5", "device": "cuda"}
    ]


def test_invalid_device_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LOCAL_BGE_DEVICE_ENV, "tpu")

    with pytest.raises(ValueError, match="Unsupported BGE device"):
        resolve_local_embedding_device()
