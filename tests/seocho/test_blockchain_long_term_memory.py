from __future__ import annotations

import pytest

from seocho.memory import (
    BlockchainLongTermMemory,
    BlockReplayConflictError,
    CausalToken,
    InMemoryTransactionRunner,
    TransactionEvent,
    opaque_ref,
)


WORKSPACE = "exchange-apac"
CHAIN = "bitcoin-mainnet"
CUSTOMER = opaque_ref("customer-42", namespace="customer")
COUNTERPARTY = opaque_ref("bc1q-risk", namespace="wallet")


def _memory() -> BlockchainLongTermMemory:
    return BlockchainLongTermMemory(InMemoryTransactionRunner())


def _event(
    *,
    block_hash: str = "block-a",
    tx_hash: str = "tx-1",
    event_index: int = 0,
    risk: tuple[str, ...] = ("sanctioned_address_exposure",),
    amount: str = "0.125",
    metadata: dict | None = None,
) -> TransactionEvent:
    return TransactionEvent(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        block_height=900_000,
        block_hash=block_hash,
        tx_hash=tx_hash,
        event_index=event_index,
        customer_ref=CUSTOMER,
        counterparty_ref=COUNTERPARTY,
        provenance_id=f"esplora:{tx_hash}:{event_index}",
        occurred_at="2026-07-11T00:00:00Z",
        asset="BTC",
        amount=amount,
        risk_reason_codes=risk,
        metadata=metadata,
    )


def test_block_replay_is_a_complete_noop() -> None:
    memory = _memory()
    event = _event()

    first = memory.reconcile_block(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        block_height=event.block_height,
        block_hash=event.block_hash,
        events=(event,),
    )
    replay = memory.reconcile_block(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        block_height=event.block_height,
        block_hash=event.block_hash,
        events=(event,),
    )

    assert first.applied is True
    assert replay.applied is False
    assert replay.causal_token == first.causal_token
    assert len(memory.event_history(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        tx_hash=event.tx_hash,
        event_index=event.event_index,
    )) == 1
    assert memory.risk_aggregate(
        workspace_id=WORKSPACE,
        customer_ref=CUSTOMER,
        counterparty_ref=COUNTERPARTY,
    ).flagged_event_count == 1
    assert len(memory.outbox_entries(workspace_id=WORKSPACE)) == 1


def test_same_block_hash_with_different_payload_fails_closed() -> None:
    memory = _memory()
    original = _event()
    memory.reconcile_block(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        block_height=original.block_height,
        block_hash=original.block_hash,
        events=(original,),
    )

    with pytest.raises(BlockReplayConflictError):
        memory.reconcile_block(
            workspace_id=WORKSPACE,
            chain_id=CHAIN,
            block_height=original.block_height,
            block_hash=original.block_hash,
            events=(_event(amount="9.9"),),
        )


def test_reorg_appends_orphan_revision_and_compensating_outbox() -> None:
    memory = _memory()
    original = _event()
    first = memory.reconcile_block(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        block_height=original.block_height,
        block_hash=original.block_hash,
        events=(original,),
    )
    replacement = _event(block_hash="block-b", tx_hash="tx-2")

    result = memory.reconcile_block(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        block_height=replacement.block_height,
        block_hash=replacement.block_hash,
        events=(replacement,),
    )

    history = memory.event_history(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        tx_hash=original.tx_hash,
        event_index=original.event_index,
    )
    assert [item.status for item in history] == ["canonical", "orphaned"]
    assert history[-1].reorged_by_block_hash == "block-b"
    assert result.causal_token.sequence == first.causal_token.sequence + 1
    assert result.orphaned_event_count == 1
    assert result.canonical_event_count == 1
    assert [entry.operation for entry in memory.outbox_entries(
        workspace_id=WORKSPACE,
        after_sequence=first.causal_token.sequence,
    )] == ["retract", "upsert"]
    # One flagged event was compensated and one replacement was added.
    assert memory.risk_aggregate(
        workspace_id=WORKSPACE,
        customer_ref=CUSTOMER,
        counterparty_ref=COUNTERPARTY,
    ).flagged_event_count == 1


def test_same_transaction_can_become_canonical_again_after_reorg() -> None:
    memory = _memory()
    original = _event()
    memory.reconcile_block(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        block_height=original.block_height,
        block_hash="block-a",
        events=(original,),
    )
    memory.reconcile_block(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        block_height=original.block_height,
        block_hash="block-b",
        events=(),
    )
    restored = _event(block_hash="block-c")
    memory.reconcile_block(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        block_height=restored.block_height,
        block_hash=restored.block_hash,
        events=(restored,),
    )

    history = memory.event_history(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        tx_hash=original.tx_hash,
        event_index=original.event_index,
    )
    assert [item.status for item in history] == [
        "canonical",
        "orphaned",
        "canonical",
    ]
    assert memory.get_current_event(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        tx_hash=original.tx_hash,
        event_index=original.event_index,
    ).status == "canonical"


def test_failed_reorg_rolls_back_every_intermediate_write() -> None:
    memory = _memory()
    original = _event()
    first = memory.reconcile_block(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        block_height=original.block_height,
        block_hash=original.block_hash,
        events=(original,),
    )
    too_large = _event(block_hash="block-b", metadata={"blob": "x" * 100_000})

    with pytest.raises(ValueError, match="90 KiB"):
        memory.reconcile_block(
            workspace_id=WORKSPACE,
            chain_id=CHAIN,
            block_height=too_large.block_height,
            block_hash=too_large.block_hash,
            events=(too_large,),
        )

    current = memory.get_current_event(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        tx_hash=original.tx_hash,
        event_index=original.event_index,
    )
    assert current is not None and current.status == "canonical"
    assert len(memory.outbox_entries(workspace_id=WORKSPACE)) == 1
    assert memory.projection_status(
        workspace_id=WORKSPACE,
        projection="risk-graph",
        required=first.causal_token,
    ).current is False


def test_projection_watermark_is_workspace_scoped_and_monotonic() -> None:
    memory = _memory()
    event = _event()
    result = memory.reconcile_block(
        workspace_id=WORKSPACE,
        chain_id=CHAIN,
        block_height=event.block_height,
        block_hash=event.block_hash,
        events=(event,),
    )

    stale = memory.projection_status(
        workspace_id=WORKSPACE,
        projection="risk-graph",
        required=result.causal_token,
    )
    acknowledged = memory.acknowledge_projection(
        workspace_id=WORKSPACE,
        projection="risk-graph",
        token=result.causal_token,
    )
    older = memory.acknowledge_projection(
        workspace_id=WORKSPACE,
        projection="risk-graph",
        token=CausalToken.for_workspace(WORKSPACE, 0),
    )

    assert stale.current is False
    assert acknowledged.current is True
    assert older.applied_sequence == result.causal_token.sequence
    with pytest.raises(ValueError, match="another workspace"):
        memory.acknowledge_projection(
            workspace_id=WORKSPACE,
            projection="risk-graph",
            token=CausalToken.for_workspace("other", 1),
        )


def test_future_projection_acknowledgement_is_rejected() -> None:
    memory = _memory()
    with pytest.raises(ValueError, match="future sequence"):
        memory.acknowledge_projection(
            workspace_id=WORKSPACE,
            projection="risk-graph",
            token=CausalToken.for_workspace(WORKSPACE, 1),
        )


def test_oversized_observation_is_rejected_before_opening_a_transaction() -> None:
    memory = BlockchainLongTermMemory(
        InMemoryTransactionRunner(), max_events_per_transaction=1
    )
    first = _event(tx_hash="tx-1")
    second = _event(tx_hash="tx-2", event_index=1)

    with pytest.raises(ValueError, match="partition the ingestion workload"):
        memory.reconcile_block(
            workspace_id=WORKSPACE,
            chain_id=CHAIN,
            block_height=first.block_height,
            block_hash=first.block_hash,
            events=(first, second),
        )
    assert memory.outbox_entries(workspace_id=WORKSPACE) == ()
