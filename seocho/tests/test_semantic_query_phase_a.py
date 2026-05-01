from seocho.query.contracts import CypherPlan
from seocho.query.cypher_validator import CypherQueryValidator
from seocho.query.insufficiency import QueryInsufficiencyClassifier
from seocho.query.intent import build_evidence_bundle, infer_question_intent
from seocho.query.strategy_chooser import ExecutionStrategyChooser, IntentSupportValidator


def test_infer_question_intent_and_evidence_bundle_contract():
    semantic_context = {
        "entities": ["Neo4j", "Cypher"],
        "matches": {
            "Neo4j": [{"display_name": "Neo4j", "database": "kgnormal", "node_id": "1", "labels": ["Database"], "source": "fulltext", "final_score": 0.9}],
            "Cypher": [{"display_name": "Cypher", "database": "kgnormal", "node_id": "2", "labels": ["Language"], "source": "fulltext", "final_score": 0.8}],
        },
    }

    intent = infer_question_intent("What is Neo4j connected to Cypher?", semantic_context["entities"])
    bundle = build_evidence_bundle(
        question="What is Neo4j connected to Cypher?",
        semantic_context={**semantic_context, "intent": intent},
        matched_entities=["Neo4j", "Cypher"],
    )

    assert intent["intent_id"] == "relationship_lookup"
    assert bundle["schema_version"] == "evidence_bundle.v2"
    assert bundle["slot_fills"]["source_entity"] == "Neo4j"
    assert bundle["slot_fills"]["target_entity"] == "Cypher"


def test_cypher_validator_and_insufficiency_classifier_contract():
    validator = CypherQueryValidator()
    classifier = QueryInsufficiencyClassifier()
    plan = CypherPlan(
        database="kgnormal",
        query="MATCH (n:Company)-[r:ACQUIRED]->(m:Company) WHERE elementId(n) = $node_id RETURN n.name AS source_entity, type(r) AS relation_type, m.name AS target_entity",
        params={"node_id": "1"},
        strategy="direct",
        anchor_entity="Apple",
        relation_types=("ACQUIRED",),
    )

    validation = validator.validate(
        plan,
        {
            "allowed_labels": ["Company"],
            "allowed_relationship_types": ["ACQUIRED"],
            "allowed_properties": ["name"],
        },
    )
    assessment = classifier.assess(
        {"intent_id": "relationship_lookup", "focus_slots": ["source_entity", "target_entity", "relation_paths"]},
        [{"source_entity": "Apple", "relation_type": "ACQUIRED", "target_entity": "Beats"}],
    )

    assert validation["ok"] is True
    assert assessment.sufficient is True
    assert assessment.reason == "sufficient"


def test_support_validator_and_strategy_chooser_contract():
    support_validator = IntentSupportValidator()
    chooser = ExecutionStrategyChooser()
    support = support_validator.assess_candidate(
        question_entity="Neo4j",
        candidate={"display_name": "Neo4j", "node_id": "1", "database": "kgnormal", "labels": ["Database"], "final_score": 0.92},
        intent={
            "intent_id": "entity_summary",
            "required_relations": [],
            "required_entity_types": ["Database"],
            "focus_slots": ["target_entity", "supporting_fact"],
        },
        constraint_slice={"graph_id": "customer360", "database": "kgnormal", "constraint_strength": "semantic_layer", "allowed_relationship_types": []},
        preview_bundle={"candidate_entities": [{"display_name": "Neo4j"}]},
    )
    decision = chooser.choose_initial(
        route="lpg",
        reasoning_mode=False,
        repair_budget=0,
        support_assessment=support,
        graph_count=1,
    )

    assert support["supported"] is True
    assert support["status"] == "supported"
    assert decision["initial_mode"] == "semantic_direct"


def test_query_insufficiency_requires_supporting_fact_when_requested():
    classifier = QueryInsufficiencyClassifier()

    assessment = classifier.assess(
        {
            "intent_id": "entity_summary",
            "focus_slots": ["target_entity", "supporting_fact"],
        },
        [{"target_entity": "Amazon"}],
    )

    assert assessment.sufficient is False
    assert assessment.reason == "partial_slot_fill"
    assert assessment.missing_slots == ("supporting_fact",)
    assert assessment.filled_slots == ("target_entity",)


def test_finalize_runtime_support_downgrades_preflight_when_runtime_slots_missing():
    support_validator = IntentSupportValidator()
    plan = CypherPlan(
        database="kgnormal",
        query="MATCH (n:Company) RETURN n.name AS target_entity",
        params={},
        strategy="entity_summary",
        anchor_entity="Amazon",
    )
    assessment = QueryInsufficiencyClassifier().assess(
        {
            "intent_id": "entity_summary",
            "focus_slots": ["target_entity", "supporting_fact"],
        },
        [{"target_entity": "Amazon"}],
    )

    support = support_validator.finalize_runtime_support(
        preflight={"supported": True, "status": "supported", "reason": "preflight_only"},
        intent={
            "intent_id": "entity_summary",
            "focus_slots": ["target_entity", "supporting_fact"],
        },
        bundle={
            "grounded_slots": ["target_entity"],
            "missing_slots": ["supporting_fact"],
            "selected_triples": [],
        },
        assessment=assessment,
        plan=plan,
        constraint_slice={"graph_id": "finder", "database": "kgnormal"},
    )

    assert support["supported"] is False
    assert support["status"] == "partial"
    assert support["reason"] == "partial_slot_fill"
    assert support["coverage"] == 0.5
    assert support["missing_slots"] == ["supporting_fact"]
    assert support["selected_triple_count"] == 0
