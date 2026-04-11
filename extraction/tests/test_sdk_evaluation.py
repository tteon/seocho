import os
import sys


ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from seocho.evaluation import ManualGoldCase, SemanticEvaluationHarness
from seocho.models import SearchResponse, SearchResult, SemanticRunResponse, DebateRunResponse


class FakeEvaluationClient:
    def search_with_context(self, query, **kwargs):
        return SearchResponse(
            results=[
                SearchResult(
                    memory_id="mem_1",
                    content="Neo4j uses Cypher.",
                    content_preview="Neo4j uses Cypher.",
                    evidence_bundle={
                        "intent_id": "relationship_lookup",
                        "slot_fills": {"source_entity": "Neo4j"},
                        "grounded_slots": ["source_entity"],
                        "missing_slots": ["target_entity", "relation_paths"],
                        "selected_triples": [],
                        "support_assessment": {"status": "partial"},
                    },
                )
            ],
            semantic_context={"entities": ["Neo4j"]},
        )

    def semantic(self, query, *, reasoning_mode=False, repair_budget=0, **kwargs):
        if reasoning_mode:
            return SemanticRunResponse(
                response="Neo4j uses Cypher.",
                route="lpg",
                support_assessment={
                    "intent_id": "relationship_lookup",
                    "supported": True,
                    "status": "supported",
                    "reason": "sufficient",
                },
                strategy_decision={"executed_mode": "semantic_repair"},
                run_metadata={"run_id": "run_repair", "recorded": True},
                evidence_bundle={
                    "intent_id": "relationship_lookup",
                    "slot_fills": {
                        "source_entity": "Neo4j",
                        "target_entity": "Cypher",
                        "relation_paths": ["USES"],
                    },
                    "grounded_slots": ["source_entity", "target_entity", "relation_paths"],
                    "missing_slots": [],
                    "selected_triples": [{"source": "Neo4j", "relation": "USES", "target": "Cypher"}],
                    "support_assessment": {"status": "supported"},
                },
            )
        return SemanticRunResponse(
            response="Neo4j is related to something.",
            route="lpg",
            support_assessment={
                "intent_id": "relationship_lookup",
                "supported": False,
                "status": "partial",
                "reason": "missing_slots",
            },
            strategy_decision={"executed_mode": "semantic_direct"},
            run_metadata={"run_id": "run_direct", "recorded": True},
            evidence_bundle={
                "intent_id": "relationship_lookup",
                "slot_fills": {"source_entity": "Neo4j"},
                "grounded_slots": ["source_entity"],
                "missing_slots": ["target_entity", "relation_paths"],
                "selected_triples": [],
                "support_assessment": {"status": "partial"},
            },
        )

    def advanced(self, query, **kwargs):
        return DebateRunResponse(
            response="Graphs disagree on the relationship.",
            debate_state="ready",
            degraded=False,
            agent_statuses=[{"graph_id": "kgnormal", "status": "ready"}],
        )


def test_semantic_evaluation_harness_scores_case_baselines():
    harness = SemanticEvaluationHarness(FakeEvaluationClient())
    case = ManualGoldCase(
        case_id="case_1",
        question="What is Neo4j connected to?",
        graph_ids=["kgnormal"],
        expected_intent_id="relationship_lookup",
        required_slots={
            "source_entity": "Neo4j",
            "target_entity": "Cypher",
            "relation_paths": "USES",
        },
        preferred_relations=["USES"],
        include_advanced=True,
    )

    result = harness.run_case(case)
    by_baseline = result.by_baseline()

    assert by_baseline["question_only_baseline"].required_answer_slot_coverage_manual == 0.0
    assert by_baseline["reference_only_baseline"].required_answer_slot_coverage_manual == 0.3333
    assert by_baseline["semantic_direct"].required_answer_slot_coverage_manual == 0.3333
    assert by_baseline["semantic_repair"].required_answer_slot_coverage_manual == 1.0
    assert by_baseline["semantic_repair"].preferred_evidence_hit_rate == 1.0
    assert by_baseline["advanced_debate"].route == "debate"


def test_semantic_evaluation_harness_aggregates_matrix_metrics():
    harness = SemanticEvaluationHarness(FakeEvaluationClient())
    cases = [
        ManualGoldCase(
            case_id="case_1",
            question="What is Neo4j connected to?",
            graph_ids=["kgnormal"],
            expected_intent_id="relationship_lookup",
            required_slots={"source_entity": "Neo4j", "target_entity": "Cypher"},
            preferred_relations=["USES"],
        )
    ]

    summary = harness.run_matrix(cases)

    assert summary.aggregate_metrics["semantic_direct"]["intent_match_rate"] == 1.0
    assert summary.aggregate_metrics["semantic_repair"]["required_answer_slot_coverage_manual"] == 1.0
    assert summary.aggregate_metrics["reference_only_baseline"]["preferred_evidence_hit_rate"] == 0.0
