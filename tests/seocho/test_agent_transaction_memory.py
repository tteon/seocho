from seocho.eval.agent_transaction_dataset import generate_agent_transaction_events
from seocho.memory import AgentTransactionMemory


class RecordingRepository:
    def __init__(self) -> None:
        self.calls = []

    def commit_revision(self, **kwargs):
        self.calls.append(kwargs)
        return "committed"


def test_agent_transaction_memory_maps_causal_state_contract() -> None:
    event = next(generate_agent_transaction_events(transaction_count=1)).to_dict()
    repository = RecordingRepository()
    memory = AgentTransactionMemory(repository)  # type: ignore[arg-type]

    assert memory.commit_event(event) == "committed"
    call = repository.calls[0]
    assert call["event_type"] == "agent_transaction.propose_order"
    assert call["allowed_previous_event_types"] == ("__initial__",)
    assert call["idempotency_key"] == event["event_id"]


def test_agent_transaction_memory_rejects_unknown_action() -> None:
    repository = RecordingRepository()
    memory = AgentTransactionMemory(repository)  # type: ignore[arg-type]
    event = next(generate_agent_transaction_events(transaction_count=1)).to_dict()
    event["action"] = "wire_money_without_policy"

    try:
        memory.commit_event(event)
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("unknown action must be rejected")
