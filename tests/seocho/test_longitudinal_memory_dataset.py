from seocho.eval.longitudinal_memory import build_gold_queries, generate_longitudinal_events


def test_longitudinal_generator_is_deterministic_and_single_user() -> None:
    first = list(generate_longitudinal_events(event_count=20, seed=7))
    second = list(generate_longitudinal_events(event_count=20, seed=7))

    assert first == second
    assert [event.sequence for event in first] == list(range(1, 21))
    assert {event.user_ref for event in first} == {"user:synthetic-001"}
    assert len({event.idempotency_key for event in first}) == 20
    assert all("raw_wallet_address" in event.private_metadata for event in first)
    assert {event.chain_id for event in first} == {"bitcoin-mainnet"}
    assert first[0].state == "intent_created"
    assert first[3].state == "intent_created"
    assert all(event.amount_sats > 0 for event in first)
    assert all(event.block_hash_ref.startswith("block:") for event in first)


def test_gold_queries_cover_memory_causality_and_context_budget() -> None:
    queries = build_gold_queries(final_sequence=100_000)
    families = {query.family for query in queries}

    assert "cross_session_memory.v1" in families
    assert "point_in_time_explanation.v1" in families
    assert "causal_transaction_status.v1" in families
    assert "long_context_optimization.v1" in families
    assert len(queries) == 12
    assert {query.query_id.split("-", 1)[0] for query in queries} == {
        f"q{index}" for index in range(1, 13)
    }
    assert all(query.expected_sequence == 100_000 for query in queries)
    assert all("raw_wallet_address" in query.denied_fields for query in queries)
