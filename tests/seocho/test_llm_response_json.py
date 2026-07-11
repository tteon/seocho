from seocho.store.llm import LLMResponse


def test_json_extracts_fenced_object_after_reasoning_preamble() -> None:
    response = LLMResponse(
        "Analysis omitted.\n```json\n{\"disposition\":\"review\",\"note\":\"a } brace\"}\n```"
    )

    assert response.json()["disposition"] == "review"


def test_json_preserves_plain_json_behavior() -> None:
    assert LLMResponse('{"ok":true}').json() == {"ok": True}
