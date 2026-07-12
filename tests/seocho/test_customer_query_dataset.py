from seocho.eval.customer_query_dataset import (
    SEEDS,
    classify_customer_query,
    detect_customer_query_boundary,
    generate_customer_query_challenges,
    generate_customer_queries,
    route_customer_query,
)


def test_customer_queries_are_english_deterministic_and_relationship_grounded() -> None:
    first = list(generate_customer_queries(count=100, seed=7))
    assert first == list(generate_customer_queries(count=100, seed=7))
    assert all(row["language"] == "en" for row in first)
    assert {row["gold"]["relationship"] for row in first} == {
        "user_to_self", "user_to_market", "user_to_network",
        "user_to_counterparty", "self_to_prior_self",
    }
    assert all(row["gold"]["required_slots"] for row in first)
    assert len(SEEDS) == 10


def test_counterparty_queries_forbid_identity_and_ownership_inference() -> None:
    counterparty = [seed for seed in SEEDS if seed.relationship == "user_to_counterparty"]
    assert counterparty
    assert all("wallet_ownership" in seed.denied_inferences for seed in counterparty)


def test_generated_customer_questions_route_to_gold_intent() -> None:
    rows = list(generate_customer_queries(count=1000, seed=11))
    correct = 0
    for row in rows:
        routed = classify_customer_query(row["question"])
        correct += routed is not None and routed.intent == row["gold"]["intent"]
    assert correct / len(rows) >= 0.90
    assert classify_customer_query("Tell me a joke") is None
    decision = route_customer_query("Tell me a joke")
    assert decision.intent is None
    assert 0 <= decision.confidence <= 1


def test_generated_questions_are_unique_and_family_split() -> None:
    rows = list(generate_customer_queries(count=10_000, seed=20260713))
    assert len({row["question"] for row in rows}) == 10_000
    assert len({row["template_family"] for row in rows}) == 50
    assert {row["split"] for row in rows} == {"evaluation", "held_out"}


def test_challenges_cover_clarify_decompose_and_reject() -> None:
    rows = list(generate_customer_query_challenges(count=300, seed=20260714))
    assert len({row["question"] for row in rows}) == 300
    assert {row["gold"]["kind"] for row in rows} == {
        "ambiguous", "multi_intent", "out_of_scope",
    }
    assert {row["gold"]["expected_action"] for row in rows} == {
        "clarify", "decompose", "reject",
    }
    guarded = [
        row for row in rows
        if row["gold"]["kind"] in {"ambiguous", "multi_intent"}
    ]
    assert guarded
    for row in guarded:
        decision = detect_customer_query_boundary(row["question"])
        assert decision is not None
        assert decision.action == row["gold"]["expected_action"]
        assert set(decision.intents) == set(row["gold"]["acceptable_intents"])
