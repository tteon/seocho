from seocho.eval.public_chain import (
    esplora_transactions_to_events,
    extract_ofac_xbt_addresses,
    group_events_by_block,
)


def test_extracts_only_xbt_digital_currency_addresses() -> None:
    xml = """<?xml version="1.0"?>
    <sdnList xmlns="https://example.test/ofac">
      <sdnEntry><idList>
        <id><idType>Digital Currency Address - XBT</idType><idNumber>bc1-risk</idNumber></id>
        <id><idType>Digital Currency Address - ETH</idType><idNumber>0x-risk</idNumber></id>
      </idList></sdnEntry>
    </sdnList>"""
    assert extract_ofac_xbt_addresses(xml) == ("bc1-risk",)


def test_esplora_mapping_uses_opaque_refs_and_confirmed_transactions_only() -> None:
    transactions = [
        {
            "txid": "tx-confirmed",
            "vin": [
                {"prevout": {"scriptpubkey_address": "bc1-risk", "value": 1000}}
            ],
            "vout": [
                {"scriptpubkey_address": "bc1-customer", "value": 900}
            ],
            "status": {
                "confirmed": True,
                "block_height": 100,
                "block_hash": "block-100",
                "block_time": 1_700_000_000,
            },
        },
        {
            "txid": "tx-mempool",
            "vin": [],
            "vout": [{"scriptpubkey_address": "bc1-risk", "value": 5}],
            "status": {"confirmed": False},
        },
    ]

    events = esplora_transactions_to_events(
        workspace_id="benchmark", risk_address="bc1-risk", transactions=transactions
    )

    assert len(events) == 1
    event = events[0]
    assert event.tx_hash == "tx-confirmed"
    assert event.amount == "900"
    assert event.customer_ref != "bc1-customer"
    assert event.counterparty_ref != "bc1-risk"
    assert event.metadata["attribution_scope"] == "address_interaction_only"
    assert group_events_by_block(events)[0][0] == (100, "block-100")
