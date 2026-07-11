from __future__ import annotations

import importlib.util
from pathlib import Path


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
