from __future__ import annotations

import pytest

from seocho.eval.text2cypher_judge import judge_receipt, judge_text2cypher
from seocho.store.llm import LLMResponse


class _Backend:
    model = "gpt-oss-120b"
    provider = "mara"

    def complete(self, **kwargs):
        assert "experimental-arm" not in kwargs["user"]
        return LLMResponse(
            text='{"intent_correct":true,"safety_compliant":true,"result_adequate":true,"score":1.0,"verdict":"pass"}'
        )


class _InconsistentBackend(_Backend):
    def complete(self, **kwargs):
        return LLMResponse(
            text='{"intent_correct":true,"safety_compliant":true,"result_adequate":true,"score":1.0,"verdict":"fail"}'
        )


def test_text2cypher_judge_has_blinded_bounded_receipt() -> None:
    cypher = "MATCH (n:Event {workspace: $workspace_id}) RETURN n LIMIT $limit"
    result = judge_text2cypher(
        _Backend(),
        model="gpt-oss-120b",
        question="List events",
        task_contract={"required_relation": "HAS_EVENT"},
        cypher=cypher,
        explain_succeeded=True,
        result_rows=1,
        result_fields=["step"],
    )
    receipt = judge_receipt(result, cypher=cypher)
    assert receipt["judge"]["verdict"] == "pass"
    assert "MATCH" not in str(receipt)
    assert receipt["semantic_quality_status"] == "llm_judge_secondary_no_gold"


def test_text2cypher_judge_rejects_inconsistent_score_and_verdict() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        judge_text2cypher(
            _InconsistentBackend(),
            model="gpt-oss-120b",
            question="List events",
            task_contract={},
            cypher="MATCH (n) RETURN n LIMIT $limit",
            explain_succeeded=True,
            result_rows=1,
            result_fields=["n"],
        )
