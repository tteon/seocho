import pytest

from seocho.eval.agent_transaction_dataset import generate_agent_transaction_events
from seocho.memory import AgentProjectionEntry, AgentTransactionProjector


class FakeRepository:
    def __init__(self, entries):
        self.entries = tuple(entries)
        self.acks = []

    def read_outbox_batch(self, **_):
        return self.entries

    def acknowledge_projection(self, **kwargs):
        self.acks.append(kwargs)


class FakeGraphStore:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def write(self, nodes, relationships, **kwargs):
        if self.fail:
            raise RuntimeError("graph unavailable")
        self.calls.append((nodes, relationships, kwargs))
        return {
            "nodes_created": len(nodes),
            "relationships_created": len(relationships),
        }


def _entries():
    events = list(generate_agent_transaction_events(transaction_count=1))
    return [
        AgentProjectionEntry(
            workspace_id=event.workspace_id,
            sequence=event.sequence,
            ordinal=0,
            aggregate_id=event.transaction_intent_id,
            payload=event.to_dict(),
        )
        for event in events
    ]


def test_projector_builds_agent_order_fill_settlement_memory_graph() -> None:
    repository = FakeRepository(_entries())
    graph = FakeGraphStore()
    projector = AgentTransactionProjector(graph_store=graph, repository=repository)

    result = projector.project_pending(
        workspace_id="okx-agent-exchange-eval", database="agent-transactions"
    )

    nodes, relationships, kwargs = graph.calls[0]
    labels = {node["label"] for node in nodes}
    rel_types = {relationship["type"] for relationship in relationships}
    assert {"Agent", "Exchange", "TransactionIntent", "Order"} <= labels
    assert {"Fill", "Settlement", "MemoryRevision"} <= labels
    assert {"ACTED_ON", "HANDED_OFF_TO", "MATERIALIZED_AS"} <= rel_types
    assert {"HAS_FILL", "SETTLED_BY", "RECORDED_AS"} <= rel_types
    assert all(relationship["source_label"] for relationship in relationships)
    assert all(relationship["target_label"] for relationship in relationships)
    assert kwargs["workspace_id"] == "okx-agent-exchange-eval"
    assert result.applied_sequence == 8
    assert repository.acks[0]["applied_sequence"] == 8


def test_projector_forwards_control_plane_fencing_token() -> None:
    repository = FakeRepository(_entries())
    projector = AgentTransactionProjector(
        graph_store=FakeGraphStore(), repository=repository
    )

    projector.project_pending(
        workspace_id="okx-agent-exchange-eval",
        database="agent-transactions",
        fencing_token=12,
    )

    assert repository.acks[0]["fencing_token"] == 12


def test_projector_does_not_ack_when_graph_write_fails() -> None:
    repository = FakeRepository(_entries())
    projector = AgentTransactionProjector(
        graph_store=FakeGraphStore(fail=True), repository=repository
    )

    with pytest.raises(RuntimeError, match="graph unavailable"):
        projector.project_pending(
            workspace_id="okx-agent-exchange-eval", database="agent-transactions"
        )

    assert repository.acks == []
