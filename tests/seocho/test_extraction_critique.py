from __future__ import annotations

import types

from seocho.index.extraction_critique import (
    CritiqueResult,
    build_recall_prompt,
    critique_extraction,
    is_enabled,
)


class _FakeLLM:
    """Routes by adversary: the recall system prompt is the one mentioning 'missed'."""

    def __init__(self, recall_reply: str, precision_reply: str):
        self.recall_reply = recall_reply
        self.precision_reply = precision_reply
        self.calls = 0

    def complete(self, *, system: str, user: str, temperature: float = 0.0):
        self.calls += 1
        reply = self.recall_reply if '"missed"' in system else self.precision_reply
        return types.SimpleNamespace(text=reply)


_EXTRACTED = {
    "nodes": [
        {"label": "Revenue", "properties": {"name": "Revenue FY2023", "value": "$5B"}},
        {"label": "LegalEntity", "properties": {"name": "Acme"}},
    ],
    "relationships": [{"source": "Acme", "target": "Revenue FY2023", "type": "REPORTED_METRIC"}],
}


def test_critique_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SEOCHO_ONTOLOGY_CRITIQUE", raising=False)
    assert is_enabled() is False
    llm = _FakeLLM("{}", "{}")
    result = critique_extraction("source", _EXTRACTED, recall_llm=llm)
    assert isinstance(result, CritiqueResult)
    assert result.enabled is False
    assert llm.calls == 0  # no LLM spend when disabled


def test_env_flag_enables(monkeypatch) -> None:
    monkeypatch.setenv("SEOCHO_ONTOLOGY_CRITIQUE", "1")
    assert is_enabled() is True
    monkeypatch.setenv("SEOCHO_ONTOLOGY_CRITIQUE", "off")
    assert is_enabled() is False


def test_recall_and_precision_parsed_and_scored() -> None:
    recall = '{"missed": [{"span": "net income was $1B", "suggested_label": "NetIncome"}, ' \
             '{"span": "gross profit $2B", "suggested_label": "GrossProfit"}]}'
    precision = '{"hallucinated": [{"item": "(:Subsidiary)", "why": "not in source"}], ' \
                '"mislabeled": []}'
    llm = _FakeLLM(recall, precision)
    result = critique_extraction("source text", _EXTRACTED, recall_llm=llm, enabled=True)
    assert result.enabled is True
    assert len(result.missed) == 2
    assert len(result.hallucinated) == 1
    assert result.extracted_node_count == 2
    # recall_proxy = missed / (missed + extracted) = 2 / (2 + 2) = 0.5
    assert result.recall_proxy == 0.5
    # precision_proxy = (2 - 1) / 2 = 0.5
    assert result.precision_proxy == 0.5
    assert llm.calls == 2  # two decorrelated passes


def test_tolerant_parsing_of_chatty_reply() -> None:
    chatty = 'Sure! Here are the gaps:\n{"missed": [{"span": "x", "suggested_label": "Revenue"}]}\nHope that helps.'
    llm = _FakeLLM(chatty, '{"hallucinated": [], "mislabeled": []}')
    result = critique_extraction("s", _EXTRACTED, recall_llm=llm, enabled=True)
    assert len(result.missed) == 1


def test_adversary_error_is_recorded_not_raised() -> None:
    class _Boom:
        def complete(self, **kwargs):
            raise RuntimeError("gateway down")

    result = critique_extraction("s", _EXTRACTED, recall_llm=_Boom(), enabled=True)
    assert result.enabled is True
    assert any("recall_adversary" in e for e in result.errors)
    # never crashes the sweep; empty findings are honest, not fabricated
    assert result.missed == []


def test_build_prompt_includes_source_and_extraction() -> None:
    prompt = build_recall_prompt("Acme reported revenue of $5B.", _EXTRACTED)
    assert "Acme reported revenue" in prompt
    assert "REPORTED_METRIC" in prompt
    assert "Revenue" in prompt
