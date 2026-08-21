"""Extraction and query spans must carry enough context to audit, and no more.

The spans recorded counts and a bare model name. That is enough to show an
extraction happened and nothing about what was asked, which ontology governed
it, or which tenant it belonged to — so a shared deployment could not filter its
own traces, and a bad extraction could not be traced back to the prompt that
produced it.

The second half matters as much as the first. Prompt and completion bodies are
content, and this package already has an opt-in policy for content: `capture_text`
returns None when capture is disabled and callers omit the field entirely. The
original fix predated that policy and would have written prompt bodies
unconditionally. Passing a prompt to `log_extraction` must not by itself decide
that it is recorded.
"""

from __future__ import annotations

import pytest

from seocho import tracing


class _Recorder(tracing.TracingBackend):
    """Captures spans instead of shipping them."""

    def __init__(self):
        self.spans = []

    def log_span(self, name, *, input_data=None, output_data=None,
                 metadata=None, tags=None, **kwargs):
        self.spans.append({
            "name": name, "input": input_data or {}, "output": output_data or {},
            "metadata": metadata or {}, "tags": list(tags or []),
        })

    def flush(self):
        pass


@pytest.fixture
def recorder(monkeypatch):
    """log_span fans out to tracing._BACKENDS, so that list is the real seam."""
    rec = _Recorder()
    monkeypatch.setattr(tracing, "_BACKENDS", [rec])
    return rec


def _extract(**kwargs):
    tracing.log_extraction(
        text_preview="some source text", ontology_name="enterprise",
        model="MiniMax-M2.5", nodes_count=4, relationships_count=2,
        score=0.9, validation_errors=0, elapsed_seconds=1.5, **kwargs,
    )


def test_span_carries_workspace_and_ontology_tags(recorder, monkeypatch):
    monkeypatch.setattr(tracing, "content_capture_enabled", lambda: False)
    _extract(workspace_id="tenant-a", provider="mara")

    assert recorder.spans, "log_extraction emitted no span"
    span = recorder.spans[-1]
    assert "workspace:tenant-a" in span["tags"]
    assert "ontology:enterprise" in span["tags"]
    assert "model:mara/MiniMax-M2.5" in span["tags"], "provider must qualify the model"


def test_prompt_body_is_omitted_when_capture_is_off(monkeypatch, recorder):
    """Passing a prompt must not be what decides it gets recorded."""
    monkeypatch.setattr(tracing, "content_capture_enabled", lambda: False)
    _extract(system_prompt="SECRET SYSTEM PROMPT",
             user_prompt="a customer's private document",
             completion="the model's answer")

    assert recorder.spans, "log_extraction emitted no span"
    span = recorder.spans[-1]
    assert "system_prompt" not in span["input"]
    assert "user_prompt" not in span["input"]
    assert "completion" not in span["output"]


def test_prompt_body_is_recorded_when_capture_is_on(monkeypatch, recorder):
    monkeypatch.setattr(tracing, "content_capture_enabled", lambda: True)
    _extract(system_prompt="SYS", user_prompt="USR", completion="OUT")

    assert recorder.spans, "log_extraction emitted no span"
    span = recorder.spans[-1]
    assert span["input"]["system_prompt"] == "SYS"
    assert span["input"]["user_prompt"] == "USR"
    assert span["output"]["completion"] == "OUT"


def test_capture_text_governs_the_new_fields():
    """Direct check that does not depend on backend routing."""
    import os

    prompt = "x" * 5000
    captured = tracing.capture_text(prompt)
    if captured is None:
        assert not tracing.content_capture_enabled()
    else:
        assert len(captured) < len(prompt), "long content must be truncated"
        assert "chars]" in captured
    assert tracing.capture_text(None) is None
