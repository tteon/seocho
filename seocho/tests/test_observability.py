from seocho.observability import StageTimer, timed_stage
from seocho.local_engine import _LocalEngine
from seocho.models import SupportAssessment
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
    assert metadata["grounding_optimizer"]["mode"] == "typed_evidence_to_answer"
    assert metadata["answer_envelope"]["grounding_optimizer"]["profiles"][0]["agent_id"] == "professor_agent"


def test_build_local_query_metadata_marks_graph_cot_mode():
    metadata = build_local_query_metadata(
        workspace_id="workspace-a",
        agent_design_pattern="",
        question="What is Neo4j connected to?",
        database="neo4j",
        ontology=type("Ontology", (), {"name": "graph"})(),
        ontology_context=_OntologyContext(),
        ontology_context_mismatch={},
        cypher="MATCH (n)-[r]->(m) RETURN n, r, m",
        params={},
        intent_data={"intent": "relationship_lookup", "anchor_entity": "Neo4j"},
        records=[{"source_entity": "Neo4j", "relation_type": "USES", "target_entity": "Cypher"}],
        answer_text="Neo4j uses Cypher.",
        attempts=[{"cypher": "MATCH ...", "result_count": 1, "error": None}],
        repair_budget=1,
        latency_breakdown_ms={"plan_ms": 1.0, "execute_ms": 2.0, "generation_ms": 0.5},
        vector_context="",
        error="",
        answer_source="llm_synthesis",
        query_mode="graph_cot",
    )

    assert metadata["query_mode"] == "graph_cot"
    assert metadata["answer_envelope"]["query_mode"] == "graph_cot"
    assert metadata["agent_pattern"]["pattern"] == "graph_cot"


def test_build_local_query_metadata_preserves_graph_context_provenance():
    metadata = build_local_query_metadata(
        workspace_id="workspace-a",
        agent_design_pattern="",
        question="Operating margin trends for Chipotle from 2021 to 2023.",
        database="neo4j",
        ontology=type("Ontology", (), {"name": "finance"})(),
        ontology_context=_OntologyContext(),
        ontology_context_mismatch={},
        cypher="MATCH (m) RETURN m",
        params={},
        intent_data={
            "intent": "financial_metric_lookup",
            "anchor_entity": "Chipotle",
            "metric_name": "operating margin",
            "years": ["2021", "2022", "2023"],
        },
        records=[],
        answer_text="Operating margin improved from 2021 to 2023.",
        attempts=[],
        repair_budget=2,
        latency_breakdown_ms={"plan_ms": 1.0, "graph_context_fallback_ms": 2.0, "generation_ms": 3.0},
        vector_context="=== Knowledge graph === Total revenue 2023 was 9,871,649.",
        error="",
        answer_source="llm_synthesis",
    )

    bundle = metadata["evidence_bundle"]
    assert bundle["slot_fills"]["supporting_fact"].startswith("=== Knowledge graph ===")
    assert bundle["slot_fills"]["period"] == ["2021", "2022", "2023"]
    assert bundle["provenance"][0]["source"] == "graph_context_fallback"
    assert "provenance_scout" not in bundle["evidence_swarm"]["critical_path"]


def test_build_local_query_metadata_inferrs_period_from_graph_context() -> None:
    metadata = build_local_query_metadata(
        workspace_id="workspace-a",
        agent_design_pattern="",
        question="EPS growth at Fiserv.",
        database="neo4j",
        ontology=type("Ontology", (), {"name": "finance"})(),
        ontology_context=_OntologyContext(),
        ontology_context_mismatch={},
        cypher="MATCH (m) RETURN m",
        params={},
        intent_data={
            "intent": "financial_metric_lookup",
            "anchor_entity": "Fiserv",
            "metric_name": "diluted EPS",
            "years": [],
        },
        records=[],
        answer_text="",
        attempts=[],
        repair_budget=2,
        latency_breakdown_ms={"graph_context_fallback_ms": 2.0},
        vector_context="EPS was $4.98 in 2023 and $3.91 in 2022.",
        error="",
        answer_source="llm_synthesis",
    )

    assert metadata["evidence_bundle"]["slot_fills"]["period"] == ["2023", "2022"]


def test_build_local_query_metadata_marks_graph_context_derivation_supported() -> None:
    metadata = build_local_query_metadata(
        workspace_id="workspace-a",
        agent_design_pattern="",
        question="Product revenue delta from 2022 to 2023.",
        database="neo4j",
        ontology=type("Ontology", (), {"name": "finance"})(),
        ontology_context=_OntologyContext(),
        ontology_context_mismatch={},
        cypher="MATCH (m) RETURN m",
        params={},
        intent_data={
            "intent": "financial_metric_delta",
            "anchor_entity": "Xylem",
            "metric_name": "product revenue",
            "years": [],
        },
        records=[],
        answer_text=(
            "The product revenue increase was calculated from evidence as "
            "$6,291 million minus $4,978 million, or $1,313 million."
        ),
        attempts=[],
        repair_budget=2,
        latency_breakdown_ms={"graph_context_fallback_ms": 2.0, "generation_ms": 3.0},
        vector_context="Revenue from products was $6,291 million in 2023 and $4,978 million in 2022.",
        error="",
        answer_source="llm_synthesis",
    )

    support = metadata["support_assessment"]
    assert support["status"] == "derived_supported"
    assert support["supported"] is True
    assert support["support_class"] == "derived_supported"
    assert support["derivation"]["support_type"] == "derived_supported"
    assert metadata["evidence_bundle"]["evidence_swarm"]["critical_path"] == []
    typed = SupportAssessment.from_dict(support)
    assert typed.supported is True
    assert typed.derivation["evidence_source"] == "graph_context_fallback"


def test_build_local_query_metadata_downgrades_derived_support_when_required_slots_remain_missing() -> None:
    metadata = build_local_query_metadata(
        workspace_id="workspace-a",
        agent_design_pattern="",
        question="STX revenue and supply chain operating performance.",
        database="neo4j",
        ontology=type("Ontology", (), {"name": "finance"})(),
        ontology_context=_OntologyContext(),
        ontology_context_mismatch={},
        cypher="MATCH (m) RETURN m",
        params={},
        intent_data={
            "intent": "financial_metric_lookup",
            "anchor_entity": "STX",
            "metric_name": "",
            "years": [],
        },
        records=[],
        answer_text=(
            "Revenue growth was calculated from $10 million and $12 million, "
            "while supply chain performance depends on lead-time evidence."
        ),
        attempts=[],
        repair_budget=2,
        latency_breakdown_ms={"graph_context_fallback_ms": 2.0, "generation_ms": 3.0},
        vector_context="Revenue was $10 million and $12 million, but no reporting period is available.",
        error="",
        answer_source="llm_synthesis",
    )

    support = metadata["support_assessment"]
    assert support["status"] == "partial"
    assert support["previous_status"] == "derived_supported"
    assert support["supported"] is False
    assert support["missing_slots"] == ["period"]
    assert metadata["evidence_bundle"]["slot_fills"]["financial_metric"] == "revenue, operating performance, supply chain"
    assert "required_slot_scout" in metadata["evidence_bundle"]["evidence_swarm"]["critical_path"]


def test_build_local_query_metadata_keeps_undercovered_graph_context_partial() -> None:
    metadata = build_local_query_metadata(
        workspace_id="workspace-a",
        agent_design_pattern="",
        question="Product revenue delta from 2022 to 2023.",
        database="neo4j",
        ontology=type("Ontology", (), {"name": "finance"})(),
        ontology_context=_OntologyContext(),
        ontology_context_mismatch={},
        cypher="MATCH (m) RETURN m",
        params={},
        intent_data={
            "intent": "financial_metric_delta",
            "anchor_entity": "Xylem",
            "metric_name": "product revenue",
            "years": [],
        },
        records=[],
        answer_text="The product revenue increased by $1,313 million.",
        attempts=[],
        repair_budget=2,
        latency_breakdown_ms={"graph_context_fallback_ms": 2.0, "generation_ms": 3.0},
        vector_context="Revenue from products was $6,291 million in 2023.",
        error="",
        answer_source="llm_synthesis",
    )

    assert metadata["support_assessment"]["status"] == "partial"
    assert metadata["support_assessment"]["supported"] is False


def test_local_graph_context_includes_workspace_id_document_content():
    class _GraphStore:
        def query(self, cypher, *, params=None, database="neo4j"):
            assert "n._workspace_id = $w OR n.workspace_id = $w" in cypher or "MATCH (a)-[x]->(b)" in cypher
            if "RETURN labels(n) AS l" in cypher:
                return [
                    {
                        "l": ["Document"],
                        "p": {
                            "workspace_id": "workspace-a",
                            "title": "Finance statement",
                            "content": "Total revenue 2023 was 9,871,649 and income from operations was 1,557,813.",
                        },
                    }
                ]
            return []

    engine = _LocalEngine.__new__(_LocalEngine)
    engine.graph_store = _GraphStore()
    engine.workspace_id = "workspace-a"

    context = engine._serialize_graph_context("neo4j")

    assert "(Document) Finance statement:" in context
    assert "Total revenue 2023 was 9,871,649" in context
