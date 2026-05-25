from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from seocho.tracing import (
    TracingBackend,
    configure_tracing_from_env,
    current_backend_names,
    disable_tracing,
    enable_tracing,
    is_backend_enabled,
    log_extraction,
    log_query,
)


class _CaptureBackend(TracingBackend):
    """Records spans in-memory so tests can assert their content."""

    def __init__(self) -> None:
        self.spans: List[Dict[str, Any]] = []

    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        self.spans.append({
            "name": name,
            "input": input_data or {},
            "output": output_data or {},
            "metadata": metadata or {},
            "tags": tags or [],
        })


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


def test_log_extraction_carries_prompt_and_semantic_tags() -> None:
    """A single extraction span carries the prompt body, completion and tags."""
    cap = _CaptureBackend()
    try:
        enable_tracing(backend=cap)
        log_extraction(
            text_preview="acme reported revenue",
            ontology_name="fibo_be+sec",
            model="kimi-k2.5",
            provider="kimi",
            workspace_id="ws-test",
            system_prompt="SYS-PROMPT",
            user_prompt="USR-PROMPT",
            completion='{"nodes": []}',
            nodes_count=3,
            relationships_count=2,
            score=0.9,
            validation_errors=0,
            elapsed_seconds=1.2,
        )

        assert len(cap.spans) == 1
        span = cap.spans[0]
        assert span["name"] == "sdk.extraction"
        # prompt body + completion on the same span as the semantic context
        assert span["input"]["system_prompt"] == "SYS-PROMPT"
        assert span["input"]["user_prompt"] == "USR-PROMPT"
        assert span["output"]["completion"] == '{"nodes": []}'
        # provider-qualified model + filterable semantic tags
        assert "model:kimi/kimi-k2.5" in span["tags"]
        assert "ontology:fibo_be+sec" in span["tags"]
        assert "workspace:ws-test" in span["tags"]
        assert "stage:extraction" in span["tags"]
        assert span["metadata"]["workspace_id"] == "ws-test"
    finally:
        disable_tracing()


def test_log_query_carries_answer_and_semantic_tags() -> None:
    """The query span is filterable by stage, ontology and workspace."""
    cap = _CaptureBackend()
    try:
        enable_tracing(backend=cap)
        log_query(
            question="What was revenue?",
            ontology_name="fibo_ind",
            model="kimi-k2.5",
            provider="kimi",
            workspace_id="ws-q",
            answer="Revenue was 100.",
            cypher="MATCH (n) RETURN n",
            result_count=1,
        )

        assert len(cap.spans) == 1
        span = cap.spans[0]
        assert span["name"] == "sdk.query"
        assert span["output"]["answer"] == "Revenue was 100."
        assert "model:kimi/kimi-k2.5" in span["tags"]
        assert "ontology:fibo_ind" in span["tags"]
        assert "workspace:ws-q" in span["tags"]
        assert "stage:query" in span["tags"]
    finally:
        disable_tracing()


def test_log_extraction_without_provider_keeps_bare_model_tag() -> None:
    """Backward-compat: no provider -> model tag stays unqualified, prompt omitted."""
    cap = _CaptureBackend()
    try:
        enable_tracing(backend=cap)
        log_extraction(
            text_preview="x",
            ontology_name="generic",
            model="gpt-4o",
            nodes_count=0,
            relationships_count=0,
            score=0.0,
            validation_errors=0,
            elapsed_seconds=0.1,
        )

        span = cap.spans[0]
        assert "model:gpt-4o" in span["tags"]
        assert "system_prompt" not in span["input"]
        assert "completion" not in span["output"]
    finally:
        disable_tracing()
