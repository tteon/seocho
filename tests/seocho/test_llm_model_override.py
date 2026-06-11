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


# --- env-gated live routing at the chokepoint (seocho-jdg) ------------------

def test_env_routing_off_by_default(monkeypatch):
    monkeypatch.delenv("SEOCHO_MODEL_ROUTING", raising=False)
    llm = _RecordingLLM()
    llm.provider = "mara"
    complete_with_task_hints(llm, system="s", user="q", task_hint="json_extraction")
    assert llm.calls[-1]["model"] is None  # OFF -> bound model untouched


def test_env_routing_routes_mapped_hints_for_mara(monkeypatch):
    monkeypatch.setenv("SEOCHO_MODEL_ROUTING", "1")
    monkeypatch.delenv("SEOCHO_MODEL_ROUTING_TIERS", raising=False)
    llm = _RecordingLLM()
    llm.provider = "mara"
    # extract/link -> BALANCED tier; answer_synthesis -> FRONTIER tier
    complete_with_task_hints(llm, system="s", user="q", task_hint="json_extraction")
    assert llm.calls[-1]["model"] == "MiniMax-M2.5"
    complete_with_task_hints(llm, system="s", user="q", task_hint="entity_linking")
    assert llm.calls[-1]["model"] == "MiniMax-M2.5"
    complete_with_task_hints(llm, system="s", user="q", task_hint="answer_synthesis")
    assert llm.calls[-1]["model"] == "MiniMax-M2.7"


def test_env_routing_skips_non_mara_provider(monkeypatch):
    monkeypatch.setenv("SEOCHO_MODEL_ROUTING", "1")
    monkeypatch.delenv("SEOCHO_MODEL_ROUTING_TIERS", raising=False)
    llm = _RecordingLLM()
    llm.provider = "openai"  # default tiers name MARA models -> guard refuses
    complete_with_task_hints(llm, system="s", user="q", task_hint="json_extraction")
    assert llm.calls[-1]["model"] is None


def test_env_routing_unmapped_hint_keeps_bound_model(monkeypatch):
    monkeypatch.setenv("SEOCHO_MODEL_ROUTING", "1")
    llm = _RecordingLLM()
    llm.provider = "mara"
    complete_with_task_hints(llm, system="s", user="q", task_hint="weird_hint")
    assert llm.calls[-1]["model"] is None


def test_env_routing_explicit_model_wins(monkeypatch):
    monkeypatch.setenv("SEOCHO_MODEL_ROUTING", "1")
    llm = _RecordingLLM()
    llm.provider = "mara"
    complete_with_task_hints(llm, system="s", user="q",
                             task_hint="json_extraction", model="forced-model")
    assert llm.calls[-1]["model"] == "forced-model"


def test_env_routing_tiers_override_skips_provider_guard(monkeypatch):
    monkeypatch.setenv("SEOCHO_MODEL_ROUTING", "1")
    monkeypatch.setenv("SEOCHO_MODEL_ROUTING_TIERS",
                       "FAST=gpt-4o-mini,BALANCED=gpt-4o,FRONTIER=gpt-4o")
    llm = _RecordingLLM()
    llm.provider = "openai"  # explicit tiers -> family is caller's choice
    complete_with_task_hints(llm, system="s", user="q", task_hint="json_extraction")
    assert llm.calls[-1]["model"] == "gpt-4o"
