"""Blockchain long-term memory contracts and transaction runners."""

from .blockchain import BlockchainLongTermMemory, BlockReplayConflictError
from .kv import FoundationDBTransactionRunner, InMemoryTransactionRunner
from .models import (
    BlockIngestResult,
    CausalToken,
    ProjectionOutboxEntry,
    ProjectionStatus,
    RiskAggregate,
    TransactionEvent,
    TransactionEventRevision,
    opaque_ref,
    workspace_token,
)

from .contracts import (
    AnswerReceipt,
    ContextEnvelope,
    MemoryRevision,
    MemoryUsageReceipt,
    TransactionState,
)
from .agent_transactions import AgentTransactionMemory
from .agent_projection import (
    AgentProjectionEntry,
    AgentProjectionResult,
    AgentTransactionProjector,
)
from .projection_format import REQUIRED_PROJECTION_PROPERTIES, validate_projection_format
from .postgres_schema import (
    POSTGRES_MEMORY_SCHEMA_SQL,
    POSTGRES_MEMORY_SCHEMA_VERSION,
    postgres_memory_schema_statements,
)
from .postgres_repository import (
    MemoryCommitResult,
    PostgreSQLMemoryRepository,
    StaleAuthoritativeMemoryError,
)

__all__ = [
    "AnswerReceipt",
    "AgentTransactionMemory",
    "AgentProjectionEntry",
    "AgentProjectionResult",
    "AgentTransactionProjector",
    "BlockchainLongTermMemory",
    "BlockIngestResult",
    "BlockReplayConflictError",
    "CausalToken",
    "ContextEnvelope",
    "FoundationDBTransactionRunner",
    "InMemoryTransactionRunner",
    "MemoryRevision",
    "MemoryCommitResult",
    "MemoryUsageReceipt",
    "POSTGRES_MEMORY_SCHEMA_SQL",
    "POSTGRES_MEMORY_SCHEMA_VERSION",
    "PostgreSQLMemoryRepository",
    "ProjectionOutboxEntry",
    "ProjectionStatus",
    "REQUIRED_PROJECTION_PROPERTIES",
    "RiskAggregate",
    "StaleAuthoritativeMemoryError",
    "TransactionEvent",
    "TransactionEventRevision",
    "TransactionState",
    "opaque_ref",
    "postgres_memory_schema_statements",
    "workspace_token",
    "validate_projection_format",
]
