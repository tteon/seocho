"""Blinded, structured LLM-as-judge contract for Text2Cypher experiments.

This evaluates a generated query against a task contract, not against a hidden
arm name. It is a secondary semantic signal: execution and deterministic
validator evidence remain primary, and a human-curated gold query/result set is
required before claiming semantic accuracy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from ..llm_structured import structured_complete


JUDGE_PROMPT_ID = "seocho.text2cypher_judge.v1"
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "intent_correct",
        "safety_compliant",
        "result_adequate",
        "score",
        "verdict",
    ],
    "properties": {
        "intent_correct": {"type": "boolean"},
        "safety_compliant": {"type": "boolean"},
        "result_adequate": {"type": "boolean"},
        "score": {"type": "number"},
        "verdict": {"type": "string", "enum": ["pass", "partial", "fail"]},
    },
}
_SYSTEM = """You are a strict, independent evaluator of a Text2Cypher result.
You receive no model or experimental-arm identity. Judge only the supplied
question, task contract, generated Cypher, and execution facts.

Rubric:
1. intent_correct: the query selects the relation/path and fields needed by the question;
2. safety_compliant: read-only, workspace scoped, parameterized values, and bounded LIMIT;
3. result_adequate: EXPLAIN succeeded and the returned row/field summary can answer the question.
Score = 1.0 only if all three hold; 0.5 for a meaningful but incomplete answer;
0.0 if unsafe, unrelated, or non-executable. Do not infer facts absent from the input.
Your fields MUST agree exactly: `pass` iff all three booleans are true and
score is 1.0; `partial` iff score is strictly between 0 and 1; `fail` iff all
three booleans are false and score is 0.0. A contradictory response is invalid.
Return JSON only with the required fields.
"""


@dataclass(frozen=True)
class Text2CypherJudgeResult:
    intent_correct: bool
    safety_compliant: bool
    result_adequate: bool
    score: float
    verdict: str
    prompt_id: str = JUDGE_PROMPT_ID

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def judge_text2cypher(
    backend: Any,
    *,
    model: str,
    question: str,
    task_contract: Mapping[str, Any],
    cypher: str,
    explain_succeeded: bool,
    result_rows: int,
    result_fields: list[str],
) -> Text2CypherJudgeResult:
    """Return a bounded judgement; caller owns local raw inputs and storage."""
    payload = structured_complete(
        backend,
        system=_SYSTEM,
        user=json.dumps(
            {
                "question": question,
                "task_contract": dict(task_contract),
                "generated_cypher": cypher,
                "execution": {
                    "explain_succeeded": bool(explain_succeeded),
                    "result_rows": max(0, int(result_rows)),
                    "result_fields": sorted({str(field) for field in result_fields}),
                },
            },
            sort_keys=True,
        ),
        schema=_SCHEMA,
        model=model,
        temperature=0.0,
        max_tokens=1024,
        task_hint="text2cypher_judge",
    )
    verdict = str(payload.get("verdict", "fail"))
    if verdict not in {"pass", "partial", "fail"}:
        raise ValueError("judge verdict must be pass, partial, or fail")
    score = max(0.0, min(1.0, float(payload.get("score", 0.0))))
    checks = (
        bool(payload.get("intent_correct")),
        bool(payload.get("safety_compliant")),
        bool(payload.get("result_adequate")),
    )
    # Syntactically valid JSON can still be internally contradictory. Treat it
    # as unavailable rather than promoting it to experimental evidence.
    if (
        (verdict == "pass" and (not all(checks) or score != 1.0))
        or (verdict == "partial" and not (0.0 < score < 1.0))
        or (verdict == "fail" and (any(checks) or score != 0.0))
    ):
        raise ValueError("judge verdict, score, and rubric checks are inconsistent")
    return Text2CypherJudgeResult(
        intent_correct=checks[0],
        safety_compliant=checks[1],
        result_adequate=checks[2],
        score=score,
        verdict=verdict,
    )


def judge_receipt(result: Text2CypherJudgeResult, *, cypher: str) -> dict[str, Any]:
    """Content-free record suitable for JSONL reports and traces."""
    return {
        "judge": result.to_dict(),
        "cypher_sha256": hashlib.sha256(cypher.encode("utf-8")).hexdigest(),
        "judge_prompt_id": JUDGE_PROMPT_ID,
        "semantic_quality_status": "llm_judge_secondary_no_gold",
    }
