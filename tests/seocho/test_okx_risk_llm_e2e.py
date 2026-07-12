from __future__ import annotations

import importlib.util
import asyncio
import json
from pathlib import Path

from seocho.store.llm import LLMResponse


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/benchmarks/okx_risk_llm_e2e.py"
SPEC = importlib.util.spec_from_file_location("okx_risk_llm_e2e", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_llm_e2e_dataset_is_ready_and_safe_without_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MARA_API_KEY", raising=False)
    report = MODULE.run(
        dataset=ROOT / "examples/okx-risk-preflight/llm_e2e_dataset.jsonl",
        limit=6,
        model="MiniMax-M2.5",
        output=tmp_path / "report.json",
    )
    assert report["status"] == "skipped"
    assert report["case_count"] == 6


def test_prompt_contains_only_disclosure_filtered_evidence() -> None:
    case = MODULE._load(ROOT / "examples/okx-risk-preflight/llm_e2e_dataset.jsonl")[0]
    prompt = MODULE._prompt(case)
    assert "raw_wallet_address" not in prompt
    assert "internal_risk_score" not in prompt
    assert "policy_threshold" not in prompt
    assert "policy_block" in prompt


def test_object_payload_unwraps_single_object_array() -> None:
    answer = {
        "disposition": "review",
        "explanation": "bounded evidence",
        "provenance_ids": ["p1"],
        "missing_information": [],
    }
    evidence_echo = {"disposition": "review", "graph_hops": 0}
    assert MODULE._object_payload([evidence_echo, answer]) == answer


def test_async_runner_skips_without_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MARA_API_KEY", raising=False)
    import asyncio

    report = asyncio.run(
        MODULE.run_async(
            dataset=ROOT / "examples/okx-risk-preflight/llm_e2e_dataset.jsonl",
            limit=2,
            model="gpt-oss-120b",
            concurrency=2,
            rounds=2,
            output=tmp_path / "async-report.json",
        )
    )
    assert report["status"] == "skipped"
    assert report["case_count"] == 4


def test_async_runner_retries_structured_output_failure(monkeypatch) -> None:
    case = MODULE._load(ROOT / "examples/okx-risk-preflight/llm_e2e_dataset.jsonl")[0]

    class FlakyBackend:
        calls = 0

        def __init__(self, *, model: str) -> None:
            self.model = model

        async def acomplete(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("malformed structured output")
            return LLMResponse(
                text=json.dumps(
                    {
                        "disposition": case["expected"]["disposition"],
                        "explanation": "bounded evidence",
                        "provenance_ids": [case["expected"]["required_provenance"]],
                        "missing_information": [],
                    }
                ),
                model=self.model,
            )

    monkeypatch.setenv("MARA_API_KEY", "test-only")
    monkeypatch.setattr(MODULE, "MaraBackend", FlakyBackend)
    report = asyncio.run(
        MODULE.run_async(
            dataset=ROOT / "examples/okx-risk-preflight/llm_e2e_dataset.jsonl",
            limit=1,
            model="test-model",
            concurrency=1,
            rounds=1,
            output=None,
            max_attempts=2,
        )
    )
    assert report["error_count"] == 0
    assert report["retry_count"] == 1
    assert report["rows"][0]["attempts"] == 2
