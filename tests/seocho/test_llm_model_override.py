"""Per-call model override in the LLM layer (seocho-jdg Part B primitive).

This is the missing capability cost-aware model routing needs on the live path:
a single request can be sent to a different tier's model without rebuilding the
client. Verified at both seams — the backend request builder and the
complete_with_task_hints helper — and kept drop-in for legacy backends.
"""

from __future__ import annotations

from seocho.store.llm import complete_with_task_hints, create_llm_backend


def test_backend_request_kwargs_honor_model_override():
    backend = create_llm_backend(provider="mara", model="MiniMax-M2.5", api_key="x")
    base = backend._completion_request_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0, max_tokens=None, response_format=None,
        reasoning_mode=None, task_hint=None,
    )
    assert base["model"] == "MiniMax-M2.5"  # default = bound model

    overridden = backend._completion_request_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0, max_tokens=None, response_format=None,
        reasoning_mode=None, task_hint=None, model="DeepSeek-V3.1",
    )
    assert overridden["model"] == "DeepSeek-V3.1"  # per-call override wins


class _RecordingLLM:
    def __init__(self):
        self.calls = []

    def complete(self, *, system, user, temperature=0.0, model=None, **kw):
        self.calls.append({"model": model, "user": user})
        return "ok"


class _LegacyLLM:
    """Predates the model kwarg — complete_with_task_hints must stay drop-in."""

    def __init__(self):
        self.calls = []

    def complete(self, *, system, user, temperature=0.0):
        self.calls.append({"user": user})
        return "ok"


def test_complete_with_task_hints_threads_model():
    llm = _RecordingLLM()
    complete_with_task_hints(llm, system="s", user="q", model="DeepSeek-V3.1")
    assert llm.calls[-1]["model"] == "DeepSeek-V3.1"


def test_complete_with_task_hints_default_passes_no_model():
    llm = _RecordingLLM()
    complete_with_task_hints(llm, system="s", user="q")
    assert llm.calls[-1]["model"] is None  # no override -> backend default used


def test_complete_with_task_hints_is_dropin_for_legacy_backends():
    llm = _LegacyLLM()
    # legacy backend rejects model= -> helper strips it and retries (no crash)
    out = complete_with_task_hints(llm, system="s", user="q", model="DeepSeek-V3.1")
    assert out == "ok" and llm.calls[-1] == {"user": "q"}
