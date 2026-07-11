"""PostgreSQL v1 schema for authoritative agent memory.

The module exposes SQL rather than opening a connection so thin SDK installs
remain dependency-free. Deployment code may execute the statements inside one
migration transaction and record ``POSTGRES_MEMORY_SCHEMA_VERSION``.
"""

from __future__ import annotations

POSTGRES_MEMORY_SCHEMA_VERSION = "agent-memory-pg.v1"

POSTGRES_MEMORY_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS agent_memory_heads (
    workspace_id text PRIMARY KEY,
    next_sequence bigint NOT NULL CHECK (next_sequence > 0)
);

CREATE TABLE IF NOT EXISTS agent_memory_revisions (
    workspace_id text NOT NULL,
    memory_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    sequence bigint NOT NULL CHECK (sequence > 0),
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    provenance_id text NOT NULL,
    payload jsonb NOT NULL,
    payload_hash text NOT NULL,
    supersedes_revision bigint,
    canonical boolean NOT NULL DEFAULT true,
    schema_version text NOT NULL,
    PRIMARY KEY (workspace_id, memory_id, revision),
    UNIQUE (workspace_id, sequence),
    CHECK (supersedes_revision IS NULL OR supersedes_revision < revision)
);

CREATE INDEX IF NOT EXISTS agent_memory_revision_sequence_idx
    ON agent_memory_revisions (workspace_id, sequence DESC);
CREATE INDEX IF NOT EXISTS agent_memory_revision_event_time_idx
    ON agent_memory_revisions (workspace_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS agent_memory_revision_current_idx
    ON agent_memory_revisions (workspace_id, memory_id, canonical)
    WHERE canonical;

CREATE TABLE IF NOT EXISTS agent_memory_idempotency (
    workspace_id text NOT NULL,
    idempotency_key text NOT NULL,
    memory_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    sequence bigint NOT NULL CHECK (sequence > 0),
    payload_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS agent_transaction_intents (
    workspace_id text NOT NULL,
    intent_id text NOT NULL,
    user_ref text NOT NULL,
    initiating_agent_ref text NOT NULL,
    state text NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    memory_sequence bigint NOT NULL CHECK (memory_sequence > 0),
    idempotency_key text NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, intent_id),
    UNIQUE (workspace_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS agent_memory_outbox (
    workspace_id text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    operation text NOT NULL CHECK (operation IN ('upsert', 'retract')),
    aggregate_type text NOT NULL,
    aggregate_id text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    claimed_by text,
    claimed_at timestamptz,
    projected_at timestamptz,
    PRIMARY KEY (workspace_id, sequence, ordinal)
);

CREATE INDEX IF NOT EXISTS agent_memory_outbox_pending_idx
    ON agent_memory_outbox (workspace_id, sequence, ordinal)
    WHERE projected_at IS NULL;

CREATE TABLE IF NOT EXISTS agent_projection_watermarks (
    workspace_id text NOT NULL,
    projection text NOT NULL,
    applied_sequence bigint NOT NULL CHECK (applied_sequence >= 0),
    fencing_token bigint NOT NULL CHECK (fencing_token >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, projection)
);

CREATE TABLE IF NOT EXISTS agent_memory_usage_receipts (
    workspace_id text NOT NULL,
    answer_id text NOT NULL,
    session_id text NOT NULL,
    intent_id text NOT NULL,
    causal_sequence bigint NOT NULL CHECK (causal_sequence >= 0),
    memory_revision_refs jsonb NOT NULL,
    evidence_refs jsonb NOT NULL,
    provenance_refs jsonb NOT NULL,
    missing_slots jsonb NOT NULL,
    support_status text NOT NULL
        CHECK (support_status IN ('supported', 'partial', 'unsupported', 'stale')),
    ontology_version text NOT NULL,
    policy_version text NOT NULL,
    prompt_version text NOT NULL,
    model text NOT NULL,
    prompt_optimization jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, answer_id)
);
""".strip()


def postgres_memory_schema_statements() -> tuple[str, ...]:
    """Return non-empty migration statements in deterministic order."""

    return tuple(
        statement.strip() + ";"
        for statement in POSTGRES_MEMORY_SCHEMA_SQL.split(";")
        if statement.strip()
    )


__all__ = [
    "POSTGRES_MEMORY_SCHEMA_SQL",
    "POSTGRES_MEMORY_SCHEMA_VERSION",
    "postgres_memory_schema_statements",
]
