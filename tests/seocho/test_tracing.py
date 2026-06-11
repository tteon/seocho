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


# ---------------------------------------------------------------------------
# Trace read / query (seocho-6q9.1)
# ---------------------------------------------------------------------------

import json

import pytest

from seocho.tracing import default_jsonl_path, read_jsonl, span_latency_ms


def _write_spans(path: Path, spans) -> None:
    path.write_text("\n".join(json.dumps(s) for s in spans) + "\n", encoding="utf-8")


def test_span_latency_ms_prefers_elapsed_seconds() -> None:
    assert span_latency_ms({"metadata": {"elapsed_seconds": 2.5}}) == 2500.0
    assert span_latency_ms({"metadata": {"total_ms": 800}}) == 800.0
    assert span_latency_ms({"metadata": {}}) is None
    assert span_latency_ms({}) is None


def test_read_jsonl_filters_by_latency_name_and_tags(tmp_path: Path) -> None:
    path = tmp_path / "seocho.jsonl"
    _write_spans(path, [
        {"timestamp": "2026-06-05T00:00:01", "name": "sdk.query", "tags": ["query"], "metadata": {"elapsed_seconds": 3.0}},
        {"timestamp": "2026-06-05T00:00:02", "name": "sdk.query", "tags": ["query"], "metadata": {"elapsed_seconds": 0.1}},
        {"timestamp": "2026-06-05T00:00:03", "name": "sdk.session.start", "tags": ["session"], "metadata": {}},
    ])

    slow = read_jsonl(path, min_latency_ms=2000)
    assert len(slow) == 1
    assert slow[0]["name"] == "sdk.query"
    assert slow[0]["latency_ms"] == 3000.0

    by_tag = read_jsonl(path, tags=["session"])
    assert [s["name"] for s in by_tag] == ["sdk.session.start"]

    by_name = read_jsonl(path, name="sdk.query")
    assert len(by_name) == 2


def test_read_jsonl_since_and_name_contains(tmp_path: Path) -> None:
    path = tmp_path / "seocho.jsonl"
    _write_spans(path, [
        {"timestamp": "2026-06-04T23:59:00", "name": "sdk.extraction", "tags": [], "metadata": {}},
        {"timestamp": "2026-06-05T00:00:05", "name": "sdk.query", "tags": [], "metadata": {}},
    ])

    recent = read_jsonl(path, since="2026-06-05T00:00:00")
    assert [s["name"] for s in recent] == ["sdk.query"]

    matched = read_jsonl(path, name_contains="extra")
    assert [s["name"] for s in matched] == ["sdk.extraction"]


def test_read_jsonl_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "seocho.jsonl"
    path.write_text(
        '{"name": "ok", "metadata": {}}\n'
        "not-json\n"
        "\n"
        '{"name": "ok2", "metadata": {}}\n',
        encoding="utf-8",
    )
    spans = read_jsonl(path)
    assert [s["name"] for s in spans] == ["ok", "ok2"]


def test_read_jsonl_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_jsonl(tmp_path / "nope.jsonl")


def test_default_jsonl_path_honours_env(monkeypatch) -> None:
    monkeypatch.delenv("SEOCHO_TRACE_JSONL_PATH", raising=False)
    assert default_jsonl_path() == "./traces/seocho.jsonl"
    monkeypatch.setenv("SEOCHO_TRACE_JSONL_PATH", "/tmp/custom.jsonl")
    assert default_jsonl_path() == "/tmp/custom.jsonl"
