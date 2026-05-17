"""Runtime shell request/response models."""

from .common import ErrorDetail, ErrorResponse
from .health import HealthComponent, HealthResponse
from .platform import (
    PlatformChatRequest,
    PlatformChatResponse,
    PlatformRawIngestRequest,
    PlatformRawIngestResponse,
    PlatformSessionResponse,
    PlatformTurn,
    RawIngestError,
    RawIngestRecord,
    RawIngestWarning,
)
from .query import (
    AgentResponse,
    DebateResponse,
    EntityOverride,
    FulltextIndexEnsureRequest,
    FulltextIndexEnsureResponse,
    FulltextIndexEnsureResult,
    QueryRequest,
    SemanticAgentResponse,
    SemanticQueryRequest,
    SemanticRunRecordListResponse,
    SemanticRunRecordResponse,
)

__all__ = [
    "AgentResponse",
    "DebateResponse",
    "EntityOverride",
    "ErrorDetail",
    "ErrorResponse",
    "FulltextIndexEnsureRequest",
    "FulltextIndexEnsureResponse",
    "FulltextIndexEnsureResult",
    "HealthComponent",
    "HealthResponse",
    "PlatformChatRequest",
    "PlatformChatResponse",
    "PlatformRawIngestRequest",
    "PlatformRawIngestResponse",
    "PlatformSessionResponse",
    "PlatformTurn",
    "QueryRequest",
    "RawIngestError",
    "RawIngestRecord",
    "RawIngestWarning",
    "SemanticAgentResponse",
    "SemanticQueryRequest",
    "SemanticRunRecordListResponse",
    "SemanticRunRecordResponse",
]
