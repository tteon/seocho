"""Tests for the provider/model-aware structured-output layer (seocho-ub5)."""

from __future__ import annotations

import pytest

from seocho.llm_structured import (
    ModelCapability,
    StructuredOutputError,
    capability_for,
    extract_json_object,
    structured_complete,
)


# ---- capability registry --------------------------------------------------

def test_capability_known_families():
    assert capability_for("DeepSeek-V3.1").family == "deepseek"
    assert capability_for("DeepSeek-V3.1").emits_reasoning is False
    assert capability_for("MiniMax-M2.7").emits_reasoning is True
    assert capability_for("MiniMax-M2.7").max_tokens_floor >= 4096
    assert capability_for("gpt-oss-120b").emits_reasoning is True
    assert capability_for("gpt-4o").supports_guided_json is True
    assert capability_for("kimi-k2.5").temperature_clamp == 1.0


def test_capability_unknown_reasoner_heuristic():
    assert capability_for("some-think-model").emits_reasoning is True
    assert capability_for("foo-r1").emits_reasoning is True
    assert capability_for("mystery").emits_reasoning is False  # conservative default


# ---- robust extraction ----------------------------------------------------

def test_extract_plain_json():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_code_fenced():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_reasoning_preamble():
    txt = 'The user wants X. Let me think... Here it is:\n{"rigid": true, "carries_identity": false}'
    assert extract_json_object(txt) == {"rigid": True, "carries_identity": False}


def test_extract_strips_think_block_and_picks_largest():
    txt = '<think>maybe {"x":1} is an example</think> final: {"rigid": true, "unity": false, "dependent": true}'
    out = extract_json_object(txt)
    assert out == {"rigid": True, "unity": False, "dependent": True}


def test_extract_raises_on_no_json():
    with pytest.raises(StructuredOutputError):
        extract_json_object("no json here at all")


# ---- structured_complete with fake backends -------------------------------

class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeBackend:
    def __init__(self, text, model=""):
        self.text = text
        self.model = model
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(self.text)


def test_structured_complete_reasoning_model_gets_suffix_and_floor():
    be = _FakeBackend('reasoning blah... {"ok": true}', model="MiniMax-M2.7")
    out = structured_complete(be, system="Do it.", user="go", max_tokens=100)
    assert out == {"ok": True}
    call = be.calls[0]
    assert "ONLY the final JSON" in call["system"]          # reasoning suffix appended
    assert call["max_tokens"] >= 4096                        # floor raised above 100
    assert call["response_format"] == {"type": "json_object"}


def test_structured_complete_clean_model_no_suffix():
    be = _FakeBackend('{"ok": true}', model="DeepSeek-V3.1")
    out = structured_complete(be, system="Do it.", user="go")
    assert out == {"ok": True}
    assert "ONLY the final JSON" not in be.calls[0]["system"]


def test_structured_complete_guided_for_openai_with_schema():
    be = _FakeBackend('{"ok": true}', model="gpt-4o")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    structured_complete(be, system="s", user="u", schema=schema)
    rf = be.calls[0]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == schema


def test_structured_complete_temperature_clamp():
    be = _FakeBackend('{"ok": true}', model="kimi-k2.5")
    structured_complete(be, system="s", user="u", temperature=0.0)
    assert be.calls[0]["temperature"] == 1.0  # clamped


class _JsonOnlyResp:
    """A backend response that pre-parses (no usable .text) — like the OntoClean
    fake. structured_complete must fall back to .json()."""
    def __init__(self, obj):
        self._obj = obj
        self.text = ""

    def json(self):
        return self._obj


def test_structured_complete_falls_back_to_json_method():
    class B:
        model = ""
        def complete(self, **kw):
            return _JsonOnlyResp({"ok": True})
    assert structured_complete(B(), system="s", user="u") == {"ok": True}
