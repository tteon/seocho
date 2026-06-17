"""Tests for the peer memory benchmark contract."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import peer_memory_benchmark as bench


def test_config_requires_bge_and_mara() -> None:
    cfg = bench.PeerBenchmarkConfig(
        dataset="cases.jsonl",
        embedding_model="text-embedding-3-small",
        answer_model="openai/gpt-4o-mini",
        judge_model="openai/gpt-4o-mini",
    )
    errors = cfg.validate()
    assert any("BGE" in err for err in errors)
    assert sum("MARA" in err for err in errors) == 2


def test_track_defaults_separate_memory_and_enterprise_systems() -> None:
    assert "graphiti" in bench.TRACK_SYSTEMS["memory"]
    assert "mem0" in bench.TRACK_SYSTEMS["memory"]
    assert "microsoft_graphrag" in bench.TRACK_SYSTEMS["enterprise_rag"]
    assert "neo4j_graphrag" in bench.TRACK_SYSTEMS["enterprise_rag"]


def test_load_cases_accepts_corpus_or_messages(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(
            {
                "case_id": "c1",
                "corpus": ["Alice assigned Bob to prepare the launch memo."],
                "question": "Who was assigned the launch memo?",
                "answer": "Bob",
                "gold_entities": ["Alice", "Bob"],
                "tags": ["decision_action_who_when_where_how"],
            }
        )
        + "\n"
        + json.dumps(
            {
                "messages": [{"role": "user", "content": "The venue changed to Seoul."}],
                "question": "Where did the venue move?",
                "answer": "Seoul",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cases = bench.load_cases(path)
    assert len(cases) == 2
    assert cases[0].case_id == "c1"
    assert cases[1].messages[0]["content"] == "The venue changed to Seoul."


def test_load_cases_rejects_rows_without_ingest_payload(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(
        json.dumps({"question": "q", "answer": "a"}) + "\n",
        encoding="utf-8",
    )
    try:
        bench.load_cases(path)
    except ValueError as exc:
        assert "corpus or messages is required" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_plan_records_fairness_controls(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(
            {
                "corpus": ["Alice decided to launch in Seoul on Monday."],
                "question": "When and where did Alice decide to launch?",
                "answer": "Monday in Seoul",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cases = bench.load_cases(path)
    cfg = bench.PeerBenchmarkConfig(dataset=str(path), systems=("seocho", "graphiti", "mem0", "cognee"))
    plan = bench.build_plan(cfg, cases)
    assert plan.dataset_cases == 1
    assert "decision_action_who_when_where_how" in plan.slices
    assert any(c.system == "graphiti" for c in plan.capabilities)
    assert any(c.system == "cognee" for c in plan.capabilities)
    assert any("same MARA answer model" in control for control in plan.fairness_controls)


def test_run_parallel_completes_seocho_and_skips_unavailable_peers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MARA_API_KEY", raising=False)
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(
            {
                "case_id": "c1",
                "corpus": ["Alice decided to launch in Seoul on Monday."],
                "question": "When and where did Alice decide to launch?",
                "answer": "Monday in Seoul",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cases = bench.load_cases(path)
    cfg = bench.PeerBenchmarkConfig(dataset=str(path), systems=("seocho", "cognee"), mode="graph_cot")
    summary = bench.run_parallel(cfg, cases, max_workers=2)
    assert summary.total_jobs == 2
    assert summary.completed_jobs == 1
    assert summary.skipped_jobs == 1
    assert all(record.mode == "graph_cot" for record in summary.records)
    seocho = next(record for record in summary.records if record.system == "seocho")
    assert seocho.status == "completed"
    assert seocho.metrics["adapter_kind"] == "seocho_contract_local"
    assert seocho.evidence


def test_mara_graphiti_client_handles_chat_completion_structured_response() -> None:
    client = object.__new__(bench._MaraChatCompletionsGraphitiClient)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"facts": ["Alice assigned Bob"]}'))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7),
    )

    parsed, input_tokens, output_tokens = client._handle_structured_response(response)

    assert parsed == {"facts": ["Alice assigned Bob"]}
    assert input_tokens == 12
    assert output_tokens == 7


def test_mara_graphiti_client_normalizes_extracted_entities_alias() -> None:
    class ExtractedEntities:
        pass

    client = object.__new__(bench._MaraChatCompletionsGraphitiClient)
    client._seocho_response_model = ExtractedEntities
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(
                        {"entities": [{"entity_name": "Northwind", "entity_type_id": 0}]}
                    )
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
    )

    parsed, _input_tokens, _output_tokens = client._handle_structured_response(response)

    assert parsed["extracted_entities"] == [
        {"entity_name": "Northwind", "entity_type_id": 0, "name": "Northwind"}
    ]


def test_mara_graphiti_client_normalizes_graphiti_edge_and_list_shapes() -> None:
    class ExtractedEdges:
        pass

    class NodeResolutions:
        pass

    client = object.__new__(bench._MaraChatCompletionsGraphitiClient)
    client._seocho_response_model = ExtractedEdges
    edges = client._normalize_structured_response(
        {"extracted_facts": [{"source": "Alice", "target": "Bob", "predicate": "assigned"}]}
    )
    assert edges["edges"][0]["source_entity_name"] == "Alice"
    assert edges["edges"][0]["target_entity_name"] == "Bob"
    assert edges["edges"][0]["relation_type"] == "assigned"

    client._seocho_response_model = NodeResolutions
    resolutions = client._normalize_structured_response([{"id": 1, "name": "Alice"}])
    assert resolutions == {"entity_resolutions": [{"id": 1, "name": "Alice"}]}
