from seocho.memory import (
    AnswerReceipt,
    CausalToken,
    ContextEnvelope,
    MemoryUsageReceipt,
    POSTGRES_MEMORY_SCHEMA_SQL,
    postgres_memory_schema_statements,
)


def test_context_envelope_detects_stale_projection() -> None:
    envelope = ContextEnvelope(
        workspace_id="ws-1",
        session_id="session-1",
        intent_id="transaction_history_explanation.v1",
        required_causal_token=CausalToken.for_workspace("ws-1", 12),
        memory_sequence_start=1,
        memory_sequence_end=12,
        graph_targets=("user-hot", "transaction-history"),
        projection_watermarks={"user-hot": 12, "transaction-history": 11},
        required_slots=("state", "provenance"),
        ontology_version="transaction-memory.v1",
        policy_version="disclosure.v1",
        prompt_version="answer.v1",
    )

    assert envelope.stale_targets == ("transaction-history",)


def test_answer_receipt_binds_usage_to_answer_and_workspace() -> None:
    usage = MemoryUsageReceipt(
        workspace_id="ws-1",
        answer_id="answer-1",
        memory_revision_refs=("memory-1:r2",),
        evidence_refs=("evidence-1",),
        provenance_refs=("source-1",),
        causal_token=CausalToken.for_workspace("ws-1", 2),
    )
    receipt = AnswerReceipt(
        workspace_id="ws-1",
        answer_id="answer-1",
        session_id="session-1",
        intent_id="point_in_time_explanation.v1",
        support_status="supported",
        usage=usage,
        ontology_version="ontology.v1",
        policy_version="policy.v1",
        prompt_version="prompt.v1",
        model="MiniMax-M2.7",
        prompt_optimization={"compression_ratio": 0.4},
    )

    assert receipt.usage.causal_token.sequence == 2


def test_postgres_schema_has_authoritative_memory_outbox_and_receipts() -> None:
    assert "agent_memory_revisions" in POSTGRES_MEMORY_SCHEMA_SQL
    assert "agent_memory_heads" in POSTGRES_MEMORY_SCHEMA_SQL
    assert "agent_memory_idempotency" in POSTGRES_MEMORY_SCHEMA_SQL
    assert "agent_memory_outbox" in POSTGRES_MEMORY_SCHEMA_SQL
    assert "agent_projection_watermarks" in POSTGRES_MEMORY_SCHEMA_SQL
    assert "agent_memory_usage_receipts" in POSTGRES_MEMORY_SCHEMA_SQL
    assert "UNIQUE (workspace_id, sequence)" in POSTGRES_MEMORY_SCHEMA_SQL
    assert len(postgres_memory_schema_statements()) >= 9
