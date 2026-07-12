from seocho.eval.customer_query_dataset import SEEDS, generate_customer_queries


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
