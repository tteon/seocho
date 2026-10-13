from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from seocho.eval.agent_observability.config import Condition, Experiment, PublicTask
from seocho.eval.agent_observability.harness import Case, Harness
from seocho.eval.agent_observability.information import Evidence
from seocho.eval.agent_observability.quality_lab import (
    Arm,
    Suite,
    analyze,
    failure_labels,
    prepare,
    run_suite,
)
from seocho.recorder.config import RecorderConfig
from seocho.recorder.mock_backend import create_mock_app
from seocho.recorder.proxy import create_app


def source_files(path: Path) -> tuple[Path, Path]:
    cases = path / "source.json"
    gold = path / "gold.json"
    cases.write_text(
        json.dumps(
            [
                {
                    "case_id": "x",
                    "question": "What is the earnings change?",
                    "fixed_passages": [
                        {
                            "evidence_id": f"filing:CO:document:p{i}",
                            "text": text,
                            "source": "one-filing",
                        }
                        for i, text in enumerate(
                            [
                                "Earnings 2022: 12; 2023: 20.",
                                "Company business facts.",
                                "Risk qualifiers.",
                            ]
                        )
                    ],
                    "agent_reference": [],
                }
            ]
        )
    )
    gold.write_text(
        json.dumps(
            {
                "x": {
                    "gold_answer": "PRIVATE_SENTINEL",
                    "metadata": {
                        "domain": "Financials",
                        "reasoning_required": True,
                        "reasoning_type": "Subtract",
                        "gold_references": ["PRIVATE_REFERENCE"],
                    },
                }
            }
        )
    )
    return cases, gold


def test_variants_preserve_inventory_gold_boundaries_and_document_split(
    tmp_path: Path,
) -> None:
    cases, gold = source_files(tmp_path)
    manifest = prepare(cases, gold, tmp_path / "prepared")
    rows = json.loads((tmp_path / "prepared/cases.json").read_text())
    assert len(rows) == 10
    assert len({x["split"] for x in manifest["cases"]}) == 1
    assert len({x["document_group"] for x in manifest["cases"]}) == 1
    assert "PRIVATE_" not in json.dumps(rows)
    original = next(x for x in rows if x["case_id"].endswith("::original"))
    for row in rows:
        if "::position_" in row["case_id"]:
            assert sorted(
                row["fixed_passages"], key=lambda r: r["evidence_id"]
            ) == sorted(original["fixed_passages"], key=lambda r: r["evidence_id"])
    private = json.loads((tmp_path / "prepared/evaluation.json").read_text())
    assert "insufficient" in private["x::missing_evidence"]["gold_answer"]
    numeric = next(x for x in rows if x["case_id"].endswith("::numeric_counterfactual"))
    assert "Fictional" in numeric["fixed_passages"][0]["text"]
    assert all(x["human_review"] == "pending" for x in manifest["cases"])
    distractor = next(
        x for x in rows if x["case_id"].endswith("::distractor_company_period")
    )
    assert distractor["fixed_passages"][:-1] == original["fixed_passages"]
    assert (
        private[distractor["case_id"]]["gold_answer"]
        == private[original["case_id"]]["gold_answer"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "independent",
        "sequential_context",
        "peer_review",
        "adaptive_review",
        "single_worker",
    ],
)
async def test_coordination_actual_context_requests_and_audits(
    tmp_path: Path, mode: str
) -> None:
    proxy = create_app(
        RecorderConfig(upstream_url="http://mock/v1", directory=tmp_path / "store"),
        transport=httpx.ASGITransport(app=create_mock_app()),
    )
    condition = Condition(
        condition_id=mode,
        strategy="planned_vanilla",
        tier_policy="forced",
        tier="T1_RETRIEVAL",
        coordination="sequential_context" if mode == "single_worker" else mode,
        worker_identity="single" if mode == "single_worker" else "per_task",
        judge_coordination=mode != "single_worker",
        delegation="single"
        if mode in ("sequential_context", "single_worker")
        else "parallel",
        fixed_tasks=[
            PublicTask(task_id="a", question="Identify the operands"),
            PublicTask(task_id="b", question="Verify the units"),
        ],
        evaluator_output_limit=2048,
    )
    experiment = Experiment(
        workspace_id="test",
        recorder_url="http://proxy/v1",
        dataset=tmp_path / "cases",
        evaluation=tmp_path / "gold",
        seeds=[42],
        evaluator_version="v2",
        conditions=[condition],
        models={
            r: "mock-fixed"
            for r in ("planner", "solver", "router", "synthesis", "evaluator")
        },
    )
    case = Case(
        case_id="x",
        question="q",
        fixed_passages=[
            Evidence(evidence_id="one", text="source facts", source="fixture")
        ],
        agent_reference=[],
        gold_answer="PRIVATE_SENTINEL",
    )
    harness = Harness(experiment, transport=httpx.ASGITransport(app=proxy))
    try:
        result = await harness.run(case, condition, 42, 0)
        assert proxy.state.store.audit(
            result["run_id"], [a["call_id"] for a in result["attempts"]]
        )["coverage_proven"]
        assert proxy.state.store.audit_agent_events(result["run_id"])["valid"]
        payloads = []
        for call in proxy.state.store.calls(
            run_id=result["run_id"], domain="inference"
        ):
            raw = proxy.state.store.blobs.get(
                call["start"]["request_blob"], "inference"
            ).decode()
            assert "PRIVATE_SENTINEL" not in raw
            request = json.loads(raw)
            payloads.append(
                json.loads(
                    next(
                        m["content"] for m in request["messages"] if m["role"] == "user"
                    )
                )
            )
        if mode == "single_worker":
            assert {
                c["start"]["tags"]["agent_id"]
                for c in proxy.state.store.calls(
                    run_id=result["run_id"], domain="inference"
                )
            } == {"single_solver"}
            assert result["coordination_metrics"]["worker_identity"] == "single"
        if mode in ("sequential_context", "single_worker"):
            received = next(
                p["received_contexts"] for p in payloads if "received_contexts" in p
            )
            assert received[0]["task_id"] == "a" and received[0]["findings"][
                "evidence_refs"
            ] == ["one"]
            assert result["coordination_metrics"]["context_transfers"] == 1
        if mode in ("peer_review", "adaptive_review"):
            reviews = [p for p in payloads if "peers" in p]
            assert len(reviews) == 2 and all(len(p["peers"]) == 1 for p in reviews)
            assert result["coordination_metrics"]["peer_reviews"] == 2
        if mode == "single_worker":
            assert "context_use" not in result["evaluation"]
        elif mode != "independent":
            assert result["evaluation"]["context_use"]["verdict"] == "unknown"
    finally:
        await harness.close()
        await proxy.state.client.aclose()
        proxy.state.store.close()


@pytest.mark.asyncio
async def test_suite_shared_admission_stops_and_partial_failure_is_saved(
    tmp_path: Path,
) -> None:
    cases, gold = source_files(tmp_path)
    prepared = tmp_path / "prepared"
    prepare(cases, gold, prepared)
    proxy = create_app(
        RecorderConfig(upstream_url="http://mock/v1", directory=tmp_path / "store"),
        transport=httpx.ASGITransport(app=create_mock_app()),
    )
    condition = Condition(
        condition_id="base",
        strategy="vanilla",
        tier_policy="forced",
        tier="T1_RETRIEVAL",
    )
    suite = Suite(
        workspace_id="test",
        recorder_url="http://proxy/v1",
        dataset=prepared / "cases.json",
        evaluation=prepared / "evaluation.json",
        manifest=prepared / "manifest.json",
        models=["mock-fixed"],
        evaluator_model="mock-fixed",
        arms=[
            Arm(
                arm_id="base",
                condition=condition,
                variants=["original", "position_back"],
            )
        ],
        split="pilot_all",
        max_calls=1,
    )
    try:
        result = await run_suite(
            suite, tmp_path / "results.json", transport=httpx.ASGITransport(app=proxy)
        )
        assert result["stopped"] and result["admitted_calls"] == 1
        assert len(result["trials"]) == 1 and result["trials"][0]["status"] == "failed"
        assert result["unexecuted_trials"] == 1
        saved = json.loads((tmp_path / "results.json").read_text())
        assert saved["trials"][0]["run_id"] == result["trials"][0]["run_id"]
    finally:
        await proxy.state.client.aclose()
        proxy.state.store.close()
    report = analyze(
        tmp_path / "results.json", tmp_path / "store", tmp_path / "analysis"
    )
    assert report["runs"][0]["correct"] is None
    assert report["runs"][0]["call_audit"]["coverage_proven"]
    assert report["model_failure_summary"][0]["failed_runs"] == 1


def test_provider_fault_is_distinct_from_semantic_judgment() -> None:
    labels = failure_labels(
        {"status": "failed", "error_type": "RateLimitError"},
        [{"call_id": "c", "finish": {"http_status": 429}}],
    )
    assert any(
        x["category"] == "provider_rate_limit" and x["basis"] == "recorded_fact"
        for x in labels
    )
    assert not any(x["category"] == "answer_incorrect_or_partial" for x in labels)


@pytest.mark.asyncio
async def test_prompt_pairs_and_report_tampering_are_verified_against_recorder(
    tmp_path: Path,
) -> None:
    cases, gold = source_files(tmp_path)
    prepared = tmp_path / "prepared"
    prepare(cases, gold, prepared)
    proxy = create_app(
        RecorderConfig(upstream_url="http://mock/v1", directory=tmp_path / "store"),
        transport=httpx.ASGITransport(app=create_mock_app()),
    )
    base = Condition(
        condition_id="base",
        strategy="vanilla",
        tier_policy="forced",
        tier="T1_RETRIEVAL",
    )
    suite = Suite(
        workspace_id="test",
        recorder_url="http://proxy/v1",
        dataset=prepared / "cases.json",
        evaluation=prepared / "evaluation.json",
        manifest=prepared / "manifest.json",
        models=["mock-fixed"],
        evaluator_model="mock-fixed",
        arms=[
            Arm(arm_id="base", condition=base, variants=["original", "position_back"]),
            Arm(
                arm_id="small",
                condition=base.model_copy(
                    update={"condition_id": "small", "hard_output_limit": 256}
                ),
            ),
            Arm(
                arm_id="evidence-first",
                condition=base.model_copy(
                    update={
                        "condition_id": "evidence-first",
                        "prompt_order": "evidence_first",
                    }
                ),
            ),
            Arm(
                arm_id="prompt-json",
                condition=base.model_copy(
                    update={
                        "condition_id": "prompt-json",
                        "output_contract": "prompt_json",
                    }
                ),
            ),
        ],
        split="pilot_all",
        max_calls=10,
    )
    try:
        result = await run_suite(
            suite, tmp_path / "results.json", transport=httpx.ASGITransport(app=proxy)
        )
        assert len(result["trials"]) == 5 and result["admitted_calls"] == 10
        first = next(t for t in result["trials"] if t["arm_id"] == "evidence-first")
        call = proxy.state.store.calls(run_id=first["run_id"], domain="inference")[0]
        request = json.loads(
            proxy.state.store.blobs.get(call["start"]["request_blob"], "inference")
        )
        payload = json.loads(
            next(m["content"] for m in request["messages"] if m["role"] == "user")
        )
        assert list(payload)[0] == "evidence"
        assert "PRIVATE_" not in json.dumps(request)
        prompt_trial = next(t for t in result["trials"] if t["arm_id"] == "prompt-json")
        prompt_call = proxy.state.store.calls(
            run_id=prompt_trial["run_id"], domain="inference"
        )[0]
        prompt_request = json.loads(
            proxy.state.store.blobs.get(
                prompt_call["start"]["request_blob"], "inference"
            )
        )
        assert not prompt_request.get("response_format")
        assert "Schema:" in str(prompt_request["messages"])
        assert prompt_trial["status"] == "completed"
    finally:
        await proxy.state.client.aclose()
        proxy.state.store.close()
    report = analyze(
        tmp_path / "results.json", tmp_path / "store", tmp_path / "analysis"
    )
    assert {"budget", "robustness", "coordination_or_prompt"} <= {
        p["kind"] for p in report["paired_observations"]
    }
    assert all(p["diagnosis"] == "unknown" for p in report["paired_observations"])
    result["trials"][0]["evaluation"]["verdict"] = "correct"
    (tmp_path / "tampered.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="judgment differs"):
        analyze(
            tmp_path / "tampered.json",
            tmp_path / "store",
            tmp_path / "tampered-analysis",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("output_contract", ["api_schema", "prompt_json"])
async def test_public_path_suite_executes_dependencies_and_audits_originals(
    tmp_path: Path,
    output_contract: str,
) -> None:
    cases, gold = source_files(tmp_path)
    prepare(cases, gold, tmp_path / "prepared")
    mock = create_mock_app()
    proxy = create_app(
        RecorderConfig(upstream_url="http://mock/v1", directory=tmp_path / "store"),
        transport=httpx.ASGITransport(app=mock),
    )
    suite = Suite(
        workspace_id="path-test",
        recorder_url="http://proxy/v1",
        dataset=tmp_path / "prepared/cases.json",
        evaluation=tmp_path / "prepared/evaluation.json",
        manifest=tmp_path / "prepared/manifest.json",
        models=["mock-fixed"],
        evaluator_model="mock-fixed",
        split="pilot_all",
        seeds=[42],
        max_calls=30,
        max_reserved_output_tokens=60000,
        arms=[
            Arm(
                arm_id=mode,
                condition=Condition(
                    condition_id=mode,
                    strategy="vanilla",
                    tier_policy="forced",
                    tier="T1_RETRIEVAL",
                    execution_path=mode,
                ),
                variants=["original"],
            )
            for mode in ("direct", "explicit", "adaptive")
        ],
    )
    try:
        await run_suite(
            suite, tmp_path / "results.json", transport=httpx.ASGITransport(app=proxy)
        )
        for call in proxy.state.store.calls():
            if call["start"]["domain"] == "inference":
                body = json.loads(
                    proxy.state.store.blobs.get(
                        call["start"]["request_blob"], "inference"
                    )
                )
                assert "PRIVATE_SENTINEL" not in json.dumps(body)
                for message in body.get("messages", []):
                    if message.get("role") == "user":
                        public = json.loads(message["content"])
                        assert not (
                            "evidence" in public and "initial_evidence" in public
                        )
        proxy.state.store.close()
        result = analyze(
            tmp_path / "results.json", tmp_path / "store", tmp_path / "analysis"
        )
        assert len(result["runs"]) == 3
        for trial in result["runs"]:
            assert trial["call_audit"]["valid"] and trial["event_audit"]["valid"]
            assert trial["status"] == "completed"
            if trial["arm_id"] != "direct":
                assert trial["execution_path"]["steps"][1]["depends_on"] == ["facts"]
                assert (
                    trial["path_evaluation"]["dependency_validity"]["verdict"]
                    == "unknown"
                )
        from seocho.recorder.views import Views

        views = Views(tmp_path / "store", "path-test")
        for row in result["runs"]:
            graph = views.graph(row["run_id"])
            if row["arm_id"] != "direct":
                assert graph["total_subtasks"] == 2
                assert any(e["kind"] == "dependency" for e in graph["edges"])
            assert (
                next(n for n in graph["nodes"] if n["kind"] == "final")["final_answer"]
                is not None
            )
        raw = json.loads((tmp_path / "results.json").read_text())
        altered = next(t for t in raw["trials"] if t["arm_id"] == "explicit")
        altered["execution_path"]["steps"][0]["answer"] = "tampered"
        (tmp_path / "tampered.json").write_text(json.dumps(raw))
        with pytest.raises(ValueError, match="Path result"):
            analyze(
                tmp_path / "tampered.json",
                tmp_path / "store",
                tmp_path / "tampered-analysis",
            )
    finally:
        proxy.state.store.close()


def test_public_plan_rejects_forward_dependencies() -> None:
    from seocho.eval.agent_observability.execution_path import PublicPathPlan

    with pytest.raises(ValueError, match="earlier-only"):
        PublicPathPlan(
            decompose=True,
            decision_summary="test",
            steps=[
                {
                    "step_id": "a",
                    "question": "test",
                    "required_slots": [],
                    "depends_on": ["b"],
                    "completion_criteria": "test",
                    "requested_capabilities": [],
                }
            ],
        )
