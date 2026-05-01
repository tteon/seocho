from seocho.observability import StageTimer, timed_stage
from seocho.query.run_metadata import build_local_query_metadata


def test_stage_timer_records_context_manager_stage():
    timer = StageTimer()

    with timer.stage("query"):
        pass
    timer.mark_total()

    payload = timer.to_dict()
    assert "query_ms" in payload
    assert "total_ms" in payload
    assert payload["query_ms"] >= 0
    assert payload["total_ms"] >= 0


def test_timed_stage_decorator_records_function_stage():
    timer = StageTimer()

    @timed_stage(timer, "helper")
    def run_helper() -> str:
        return "ok"

    assert run_helper() == "ok"
    assert "helper_ms" in timer.to_dict()


class _OntologyContext:
    def metadata(self, *, usage: str):
        return {"usage": usage, "context_hash": "ctx"}


def test_build_local_query_metadata_mirrors_runtime_envelope():
    metadata = build_local_query_metadata(
        workspace_id="workspace-a",
        agent_design_pattern="",
        question="What was PTC revenue in 2023?",
        database="neo4j",
        ontology=type("Ontology", (), {"name": "finance"})(),
        ontology_context=_OntologyContext(),
        ontology_context_mismatch={},
        cypher="MATCH (n) RETURN n",
        params={},
        intent_data={
            "intent": "financial_metric_lookup",
            "anchor_entity": "PTC",
            "metric_name": "revenue",
            "years": ["2023"],
        },
        records=[{"company": "PTC", "metric_name": "revenue", "year": "2023", "supporting_fact": "PTC revenue was $2.1B."}],
        answer_text="For PTC, revenue was $2.1B in 2023.",
        attempts=[],
        repair_budget=0,
        latency_breakdown_ms={"plan_ms": 1.0, "execute_ms": 2.0, "deterministic_answer_ms": 0.5},
        vector_context="",
        error="",
        answer_source="deterministic",
    )

    assert metadata["schema_version"] == "query_run_metadata.v1"
    assert metadata["latency_breakdown_ms"]["retrieval_ms"] == 3.0
    assert metadata["latency_breakdown_ms"]["generation_ms"] == 0.5
    assert metadata["support_assessment"]["status"] == "supported"
    assert metadata["evidence_bundle"]["slot_fills"]["financial_metric"] == "revenue"
    assert metadata["answer_envelope"]["schema_version"] == "answer_envelope.v1"
    assert metadata["agent_pattern"]["pattern"] == "semantic_direct"
