"""Deterministic interface gates; these do not measure MARA/model quality."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request

from seocho.eval.agent_observability.config import Condition, Experiment
from seocho.eval.agent_observability.harness import Case, Harness, HarnessRunError
from seocho.eval.agent_observability.information import Evidence
from seocho.eval.agent_observability.tool_agent import ToolState
from seocho.recorder.config import RecorderConfig
from seocho.recorder.mock_backend import create_mock_app
from seocho.recorder.proxy import create_app


def test_receipt_dependencies_and_source_numbers() -> None:
    state = ToolState()
    state.evidence["a"] = {"text": "2019: 2,294 million; 2018: 1,661 million"}
    with pytest.raises(ValueError, match="search"):
        state.extract("invented", "2294", "million", "2019")
    with pytest.raises(ValueError, match="occur"):
        state.extract("a", "999", "million", "2019")
    with pytest.raises(ValueError, match="receipt IDs"):
        state.calculate("subtract", "invented", "invented")
    a = state.extract("a", "2294", "million", "2019")
    b = state.extract("a", "1661", "million", "2018")
    result = state.calculate("subtract", a["receipt_id"], b["receipt_id"])
    assert result["result"] == "633"
    assert result["units_and_periods"] == "not_verified"
    assert (
        state.metrics()["state_success"] == "not_scored_without_precommitted_case_goals"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("max_calls", [8, 6])
@pytest.mark.parametrize("metacognition", [False, True])
async def test_model_tool_loop_records_parallel_operands_and_delegate(
    tmp_path: Path, max_calls: int, metacognition: bool
) -> None:
    backend = FastAPI()
    mock = create_mock_app()

    @backend.post("/v1/chat/completions")
    async def completion(request: Request) -> dict:
        body = await request.json()
        if not body.get("tools"):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mock)
            ) as client:
                response = await client.post(
                    "http://mock/v1/chat/completions", json=body
                )
                return response.json()
        messages = body["messages"]
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        calls = []
        if not results:
            calls = [("search_evidence", {"query": "fixed evidence"})]
        elif len(results) == 1:
            calls = [
                (
                    "extract_number",
                    {
                        "evidence_id": "a",
                        "value": value,
                        "unit": "million",
                        "period": period,
                    },
                )
                for value, period in [("2294", "2019"), ("1661", "2018")]
            ]
        elif len(results) == 3:
            calls = [
                (
                    "calculate",
                    {
                        "operation": "subtract",
                        "left_id": results[1]["receipt_id"],
                        "right_id": results[2]["receipt_id"],
                    },
                )
            ]
        elif len(results) == 4 and not metacognition:
            calls = [("delegate", {"subtask": "Check the supporting source"})]
        message = {
            "role": "assistant",
            "content": None
            if calls
            else json.dumps({"answer": "633 million", "evidence_refs": ["a"]}),
        }
        if calls:
            message["tool_calls"] = [
                {
                    "id": f"tc-{len(results)}-{i}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for i, (name, args) in enumerate(calls)
            ]
        return {
            "id": f"completion-{len(results)}",
            "object": "chat.completion",
            "created": 0,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls" if calls else "stop",
                    "message": message,
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }

    proxy = create_app(
        RecorderConfig(upstream_url="http://backend/v1", directory=tmp_path),
        transport=httpx.ASGITransport(app=backend),
    )
    condition = Condition(
        condition_id="tools",
        strategy="tool_agent",
        tier_policy="forced",
        tier="T1_RETRIEVAL",
        max_calls=max_calls,
        metacognition=metacognition,
    )
    experiment = Experiment(
        workspace_id="test",
        recorder_url="http://proxy/v1",
        dataset=tmp_path / "cases",
        evaluation=tmp_path / "gold",
        seeds=[42],
        models={
            r: "mock-fixed"
            for r in ("planner", "solver", "router", "synthesis", "evaluator")
        },
        conditions=[condition],
        evaluator_version="v2",
    )
    case = Case(
        case_id="x",
        question="What is the difference?",
        fixed_passages=[
            Evidence(
                evidence_id="a",
                text="2019: 2,294 million; 2018: 1,661 million",
                source="fixture",
            )
        ],
        agent_reference=[],
        gold_answer="633 million",
    )
    harness = Harness(experiment, transport=httpx.ASGITransport(app=proxy))
    try:
        if max_calls == 6:
            with pytest.raises(HarnessRunError) as caught:
                await harness.run(case, condition, 42, 0)
            result = caught.value.report
            assert result["status"] == "failed"
            assert "BUDGET_EXHAUSTED" in result["flags"]
            assert len(proxy.state.store.calls(run_id=result["run_id"])) == 6
            assert proxy.state.store.audit(result["run_id"])["valid"]
            assert proxy.state.store.audit_agent_events(result["run_id"])["valid"]
            return
        result = await harness.run(case, condition, 42, 0)
        events = [
            json.loads(proxy.state.store.blobs.get(e["payload_blob"], e["domain"]))
            for e in proxy.state.store.agent_events(result["run_id"])
            if e["action"] == "tool_call"
        ]
        assert result["tool_metrics"]["successful_calls"] == (
            4 if metacognition else 5
        ), [e for e in events if e.get("status") == "failed"]
        assert result["tool_metrics"]["completed_delegations"] == (
            0 if metacognition else 1
        )
        if metacognition:
            assert result["initial_assessment"]["answerable"] is False
            assert result["initial_evaluation"]["verdict"] == "unknown"
            assert result["metacognition_metrics"]["recovery"] is None
            for call in proxy.state.store.calls(run_id=result["run_id"]):
                if call["start"]["domain"] == "inference":
                    raw = json.loads(
                        proxy.state.store.blobs.get(
                            call["start"]["request_blob"], "inference"
                        )
                    )
                    assert "gold_answer" not in json.dumps(raw)
                    assert "delegate" not in [
                        t["function"]["name"] for t in raw.get("tools", [])
                    ]
        assert result["tool_metrics"]["completed_calculations"] == 1
        assert result["evaluation"]["tool_relevance"]["verdict"] == "unknown"
        assert len(proxy.state.store.calls(run_id=result["run_id"])) == 7
        assert proxy.state.store.audit(result["run_id"])["valid"]
        assert proxy.state.store.audit_agent_events(result["run_id"])["valid"]
    finally:
        await harness.close()
        proxy.state.store.close()


def test_epistemic_outcomes_preserve_unknown_and_validate_refs() -> None:
    from seocho.eval.agent_observability.metacognition import (
        EpistemicAssessment,
        paired_outcome,
    )

    assessment = EpistemicAssessment(
        answer="Insufficient evidence",
        evidence_refs=[],
        answerable=False,
        confidence=0.2,
        required_slots=[
            {
                "slot_id": "x",
                "description": "cost",
                "status": "missing",
                "evidence_refs": ["bad"],
            }
        ],
        unsupported_assumptions=[],
        requested_capabilities=["knowledge_graph"],
    )
    with pytest.raises(ValueError, match="invented"):
        assessment.validate_evidence({"real"})
    assert paired_outcome("incorrect", "correct", assessment)["recovery"] is True
    assert paired_outcome("partial", "correct", assessment)["recovery"] is None
    assert paired_outcome("correct", "incorrect", assessment)["regression"] is True
    with pytest.raises(ValueError, match="single tool_agent"):
        Condition(
            condition_id="bad",
            strategy="vanilla",
            tier_policy="forced",
            tier="T0_PARAMETRIC",
            metacognition=True,
        )


def test_repeated_search_retains_source_without_duplicate_prompt_text() -> None:
    state = ToolState()
    row = {"evidence_id": "a", "source": "fixture", "text": "2023 earnings 20"}
    assert state.deliver([row]) == [row]
    assert state.deliver([row]) == [
        {"evidence_id": "a", "source": "fixture", "already_delivered": True}
    ]
    assert state.extract("a", "20", "million", "2023")["value"] == "20"
    changed = {**row, "text": "2023 earnings 21"}
    assert state.deliver([changed]) == [changed]
