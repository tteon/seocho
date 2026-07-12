from seocho.eval.agent_memory_queries import (
    AGENT_MEMORY_QUERIES,
    build_augmented_prompt,
    classify_agent_memory_query,
    compile_agent_memory_query,
)
from seocho.eval.exchange_calibrated import generate_exchange_calibrated_events


def test_exchange_calibrated_dataset_is_deterministic_and_covers_failures() -> None:
    first = list(generate_exchange_calibrated_events(intent_count=1000, seed=7))
    second = list(generate_exchange_calibrated_events(intent_count=1000, seed=7))
    assert first == second
    scenarios = {event.scenario for event in first}
    assert {"cancel_fill_race", "duplicate_stream", "out_of_order", "policy_drift"} <= scenarios
    assert any(event.duplicate for event in first)
    assert any(event.late for event in first)
    assert all(event.private_metadata["raw_account_id"].startswith("never-export:") for event in first)


def test_six_queries_cover_transaction_memory_failure_modes() -> None:
    assert len(AGENT_MEMORY_QUERIES) == 6
    assert {query.query_id for query in AGENT_MEMORY_QUERIES} == {
        "current-state", "point-in-time", "cancel-fill-race", "agent-handoff",
        "projection-lag", "long-context",
    }
    assert all(query.max_hops <= 4 for query in AGENT_MEMORY_QUERIES)


def test_user_questions_route_to_audited_recipes_and_augmented_prompts() -> None:
    for query in AGENT_MEMORY_QUERIES:
        classified = classify_agent_memory_query(query.question)
        assert classified == query
        plan = compile_agent_memory_query(query, workspace_id="ws-1", intent_id="intent-1")
        assert plan.tier == "approved_recipe"
        assert "$workspace_id" in plan.cypher
        assert plan.params["limit"] == 50
        system, suffix, metadata = build_augmented_prompt(
            query, evidence={"state": "filled", "support_status": "supported"}
        )
        assert "filled" not in system
        assert "filled" in suffix
        assert metadata["query_id"] == query.query_id


def test_unsupported_or_ambiguous_question_does_not_execute() -> None:
    assert classify_agent_memory_query("비트코인 가격이 어떻게 되나요?") is None
    assert classify_agent_memory_query("현재 주문 상태와 agent 경로를 같이 보여줘") is None
