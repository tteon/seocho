from seocho.eval.agent_transaction_dataset import (
    generate_agent_transaction_events,
    normalize_okx_order_row,
)


def test_agent_transaction_dataset_preserves_causal_handoffs() -> None:
    events = list(generate_agent_transaction_events(transaction_count=3))
    first = [
        event
        for event in events
        if event.transaction_intent_id == events[0].transaction_intent_id
    ]

    assert [event.action for event in first] == [
        "propose_order",
        "approve_order",
        "place_order",
        "ack_order",
        "partial_fill",
        "fill_order",
        "settle_position",
        "publish_memory",
    ]
    assert first[0].causal_parent_id is None
    assert all(
        current.causal_parent_id == previous.event_id
        for previous, current in zip(first, first[1:])
    )
    assert all(event.simulation for event in events)
    assert {event.instrument_id for event in events} == {"BTC-USDT-SWAP"}
    assert any(event.decision == "canceled" for event in events)
    assert any(event.decision == "rejected" for event in events)


def test_okx_normalizer_hashes_exchange_order_id_and_omits_credentials() -> None:
    normalized = normalize_okx_order_row(
        {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "clOrdId": "seocho-1",
            "ordId": "505073046126960640",
            "state": "filled",
            "side": "buy",
            "posSide": "net",
            "ordType": "limit",
            "sz": "2",
            "px": "60000",
            "accFillSz": "2",
            "avgPx": "59995",
            "uTime": "1",
            "apiKey": "must-not-survive",
        },
        workspace_id="ws-1",
        sequence=9,
    )

    assert normalized["exchange_order_ref"].startswith("okx-order:")
    assert "505073046126960640" not in repr(normalized)
    assert "apiKey" not in normalized
