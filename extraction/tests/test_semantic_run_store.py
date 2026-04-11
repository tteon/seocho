import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from semantic_run_store import get_semantic_run, list_semantic_runs, save_semantic_run


def test_semantic_run_store_roundtrip(tmp_path):
    base_dir = tmp_path / "semantic_metadata"
    record = {
        "run_id": "run_123",
        "timestamp": "2026-04-11T10:00:00Z",
        "workspace_id": "default",
        "query_preview": "What is Neo4j connected to?",
        "query_hash": "hash_123",
        "route": "lpg",
        "intent_id": "relationship_lookup",
        "support_assessment": {
            "status": "supported",
            "reason": "sufficient",
            "coverage": 1.0,
        },
        "strategy_decision": {"executed_mode": "semantic_direct"},
        "reasoning": {"requested": False},
        "evidence_summary": {"grounded_slots": ["source_entity", "target_entity"]},
        "lpg_record_count": 1,
        "rdf_record_count": 0,
        "response_preview": "Neo4j uses Cypher.",
    }

    stored = save_semantic_run(record, base_dir=str(base_dir))
    rows = list_semantic_runs("default", base_dir=str(base_dir))
    fetched = get_semantic_run("default", "run_123", base_dir=str(base_dir))

    assert stored["run_id"] == "run_123"
    assert stored["db_path"].endswith("semantic_runs.db")
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run_123"
    assert rows[0]["support_status"] == "supported"
    assert fetched["strategy_decision"]["executed_mode"] == "semantic_direct"
