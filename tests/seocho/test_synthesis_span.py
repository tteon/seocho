"""ADR-0144 / seocho-d6x.6: gen_ai.* + prompt/cache identity on rag.synthesize.

External-API deployments control the prompt, so the joinable signals are model,
params, token usage, and the cacheable system-prompt prefix hash.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from seocho.local_engine import _LocalEngine
from seocho.query.answering import QueryAnswerSynthesizer
from seocho.tracing import (
    TracingBackend,
    disable_tracing,
    enable_tracing,
    start_span,
)


class _Recorder(TracingBackend):
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
        self.spans.append({"name": name, "metadata": dict(metadata or {})})


def test_synthesize_stashes_usage_and_temperature() -> None:
    class _Resp:
        text = "the answer"
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    class _LLM:
        model = "mara/MiniMax-M2.5"

        def complete(self, **_kw: Any) -> Any:
            return _Resp()

    class _Strategy:
        def render_answer(self, _q: str, _recs: str) -> Any:
            return ("system prompt", "user prompt")

    synth = QueryAnswerSynthesizer(query_strategy=_Strategy(), llm=_LLM())
    out = synth.synthesize("q?", [{"a": 1}])

    assert out == "the answer"
    assert synth.last_usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert synth.last_temperature == 0.1


def test_annotate_synthesis_span_sets_gen_ai_and_cache_hashes() -> None:
    rec = _Recorder()
    engine = SimpleNamespace(
        llm=SimpleNamespace(model="mara/MiniMax-M2.5", provider="mara")
    )
    synth = SimpleNamespace(
        last_temperature=0.1,
        last_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    octx = SimpleNamespace(
        kv_cache_layout=lambda: {"stable_prefix_hash": "abc123", "context_hash": "ctx99"}
    )
    try:
        enable_tracing(backend=rec)
        with start_span("rag.synthesize") as span:
            _LocalEngine._annotate_synthesis_span(engine, span, synth, octx)
    finally:
        disable_tracing()

    md = next(s["metadata"] for s in rec.spans if s["name"] == "rag.synthesize")
    assert md["gen_ai.request.model"] == "mara/MiniMax-M2.5"
    assert md["gen_ai.system"] == "mara"
    assert md["gen_ai.request.temperature"] == 0.1
    assert md["gen_ai.usage.input_tokens"] == 10
    assert md["gen_ai.usage.output_tokens"] == 5
    assert md["gen_ai.usage.total_tokens"] == 15
    assert md["stable_prefix_hash"] == "abc123"
    assert md["ontology_context_hash"] == "ctx99"


def test_annotate_synthesis_span_is_noop_when_disabled() -> None:
    disable_tracing()
    engine = SimpleNamespace(llm=SimpleNamespace(model="m"))
    synth = SimpleNamespace(last_temperature=None, last_usage=None)
    octx = SimpleNamespace(kv_cache_layout=lambda: {})
    # null span + tracing disabled: must not raise
    with start_span("rag.synthesize") as span:
        _LocalEngine._annotate_synthesis_span(engine, span, synth, octx)
