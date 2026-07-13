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
from .sequence import CausalFrontier, CausalPosition, SequenceMode, SequencePolicy
from .telemetry import MemoryCommitMetricsObserver

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
    CommitPhaseObserver,
    MemoryCommitResult,
    PostgreSQLMemoryRepository,
    ProjectionFencingError,
    StaleAuthoritativeMemoryError,
)
from .postgres_sequence_v2 import (
    AllocatedPosition,
    CausalOutboxEntry,
    POSTGRES_SEQUENCE_V2_SCHEMA_SQL,
    POSTGRES_SEQUENCE_V2_SCHEMA_VERSION,
    PostgreSQLCausalSequenceAllocator,
    PostgreSQLCausalProjectionRepository,
    postgres_sequence_v2_schema_statements,
)

__all__ = [
    "AnswerReceipt",
    "AllocatedPosition",
    "AgentTransactionMemory",
    "AgentProjectionEntry",
    "AgentProjectionResult",
    "AgentTransactionProjector",
    "BlockchainLongTermMemory",
    "BlockIngestResult",
    "BlockReplayConflictError",
    "CausalToken",
    "CausalFrontier",
    "CausalOutboxEntry",
    "CausalPosition",
    "CommitPhaseObserver",
    "ContextEnvelope",
    "FoundationDBTransactionRunner",
    "InMemoryTransactionRunner",
    "MemoryRevision",
    "MemoryCommitResult",
    "MemoryCommitMetricsObserver",
    "MemoryUsageReceipt",
    "POSTGRES_MEMORY_SCHEMA_SQL",
    "POSTGRES_MEMORY_SCHEMA_VERSION",
    "POSTGRES_SEQUENCE_V2_SCHEMA_SQL",
    "POSTGRES_SEQUENCE_V2_SCHEMA_VERSION",
    "PostgreSQLMemoryRepository",
    "PostgreSQLCausalSequenceAllocator",
    "PostgreSQLCausalProjectionRepository",
    "ProjectionFencingError",
    "ProjectionOutboxEntry",
    "ProjectionStatus",
    "REQUIRED_PROJECTION_PROPERTIES",
    "RiskAggregate",
    "SequenceMode",
    "SequencePolicy",
    "StaleAuthoritativeMemoryError",
    "TransactionEvent",
    "TransactionEventRevision",
    "TransactionState",
    "opaque_ref",
    "postgres_memory_schema_statements",
    "postgres_sequence_v2_schema_statements",
    "workspace_token",
    "validate_projection_format",
]
