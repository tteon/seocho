from __future__ import annotations

from pathlib import Path

from seocho.tracing import (
    configure_tracing_from_env,
    current_backend_names,
    disable_tracing,
    enable_tracing,
    is_backend_enabled,
)


def test_enable_tracing_none_disables_all_backends() -> None:
    try:
        enable_tracing(backend="console")
        assert current_backend_names() == ["console"]

        enabled = enable_tracing(backend="none")

        assert enabled is False
        assert current_backend_names() == []
        assert is_backend_enabled("opik") is False
    finally:
        disable_tracing()


def test_configure_tracing_from_env_uses_jsonl_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "trace.jsonl"
    monkeypatch.setenv("SEOCHO_TRACE_BACKEND", "jsonl")
    monkeypatch.setenv("SEOCHO_TRACE_JSONL_PATH", str(output))

    try:
        enabled = configure_tracing_from_env()

        assert enabled is True
        assert current_backend_names() == ["jsonl"]
        assert is_backend_enabled("jsonl") is True
    finally:
        disable_tracing()


def test_configure_tracing_from_env_rejects_invalid_backend(monkeypatch) -> None:
    monkeypatch.setenv("SEOCHO_TRACE_BACKEND", "unsupported")

    try:
        enabled = configure_tracing_from_env()

        assert enabled is False
        assert current_backend_names() == []
    finally:
        disable_tracing()
