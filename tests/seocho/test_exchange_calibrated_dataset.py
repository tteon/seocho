from seocho.eval.exchange_calibrated import generate_exchange_calibrated_events


def test_generator_is_deterministic_and_cross_venue() -> None:
    first = list(generate_exchange_calibrated_events(intent_count=30, seed=7))
    second = list(generate_exchange_calibrated_events(intent_count=30, seed=7))
    assert first == second
    assert {event.venue for event in first} == {"okx", "binance", "coinbase"}
    assert all(event.chain_anchor_ref.startswith("bitcoin-tx:") for event in first)


def test_faults_are_explicit_not_mislabeled_as_observed() -> None:
    events = list(generate_exchange_calibrated_events(intent_count=1000, seed=11))
    faults = [event for event in events if event.duplicate or event.late or event.step in {"transport_timeout", "sequence_gap"}]
    assert faults
    assert all(event.evidence_class == "fault_injected" for event in faults)
    assert all(event.evidence_class != "observed_public_chain" for event in events)


def test_venue_terminal_states_follow_documented_vocabularies() -> None:
    events = list(generate_exchange_calibrated_events(intent_count=300, seed=19))
    filled = {(event.venue, event.venue_state) for event in events if event.step == "filled"}
    assert filled == {("okx", "filled"), ("binance", "FILLED"), ("coinbase", "FILLED")}
