from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class JsonSerializable:
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Memory(JsonSerializable):
    memory_id: str
    workspace_id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = ""
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    database: Optional[str] = None
    content_preview: str = ""
    source_type: str = ""
    category: str = ""
    entities: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Memory":
        return cls(**payload)


@dataclass(slots=True)
class SearchResult(JsonSerializable):
    memory_id: str
    content: str
    content_preview: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    matched_entities: List[str] = field(default_factory=list)
    database: str = ""
    status: str = ""
    evidence_bundle: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SearchResult":
        return cls(**payload)


@dataclass(slots=True)
class MemoryCreateResult(JsonSerializable):
    memory: Memory
    ingest_summary: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "MemoryCreateResult":
        return cls(
            memory=Memory.from_dict(payload["memory"]),
            ingest_summary=dict(payload.get("ingest_summary", {})),
            trace_id=str(payload.get("trace_id", "")),
        )


@dataclass(slots=True)
class SearchResponse(JsonSerializable):
    results: List[SearchResult] = field(default_factory=list)
    semantic_context: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SearchResponse":
        return cls(
            results=[SearchResult.from_dict(item) for item in payload.get("results", [])],
            semantic_context=dict(payload.get("semantic_context", {})),
            trace_id=str(payload.get("trace_id", "")),
        )


@dataclass(slots=True)
class ChatResponse(JsonSerializable):
    assistant_message: str
    memory_hits: List[Dict[str, Any]] = field(default_factory=list)
    search_results: List[SearchResult] = field(default_factory=list)
    semantic_context: Dict[str, Any] = field(default_factory=dict)
    evidence_bundle: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ChatResponse":
        return cls(
            assistant_message=str(payload.get("assistant_message", "")),
            memory_hits=list(payload.get("memory_hits", [])),
            search_results=[SearchResult.from_dict(item) for item in payload.get("search_results", [])],
            semantic_context=dict(payload.get("semantic_context", {})),
            evidence_bundle=dict(payload.get("evidence_bundle", {})),
            trace_id=str(payload.get("trace_id", "")),
        )


@dataclass(slots=True)
class ArchiveResult(JsonSerializable):
    memory_id: str
    workspace_id: str
    database: str
    status: str
    archived_at: str
    archived_nodes: int
    trace_id: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ArchiveResult":
        return cls(**payload)


@dataclass(slots=True)
class GraphTarget(JsonSerializable):
    graph_id: str
    database: str
    uri: str
    ontology_id: str
    vocabulary_profile: str
    description: str = ""
    workspace_scope: str = "default"

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "GraphTarget":
        return cls(**payload)


@dataclass(slots=True)
class AgentRunResponse(JsonSerializable):
    response: str
    trace_steps: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AgentRunResponse":
        return cls(
            response=str(payload.get("response", "")),
            trace_steps=list(payload.get("trace_steps", [])),
        )


@dataclass(slots=True)
class EntityOverride(JsonSerializable):
    question_entity: str
    database: str
    node_id: str | int
    display_name: Optional[str] = None
    labels: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "EntityOverride":
        return cls(
            question_entity=str(payload.get("question_entity", "")).strip(),
            database=str(payload.get("database", "")).strip(),
            node_id=payload.get("node_id", ""),
            display_name=str(payload.get("display_name", "")).strip() or None,
            labels=[str(item) for item in payload.get("labels", [])],
        )


@dataclass(slots=True)
class SemanticRunResponse(JsonSerializable):
    response: str
    route: str
    trace_steps: List[Dict[str, Any]] = field(default_factory=list)
    semantic_context: Dict[str, Any] = field(default_factory=dict)
    lpg_result: Optional[Dict[str, Any]] = None
    rdf_result: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SemanticRunResponse":
        return cls(
            response=str(payload.get("response", "")),
            route=str(payload.get("route", "")),
            trace_steps=list(payload.get("trace_steps", [])),
            semantic_context=dict(payload.get("semantic_context", {})),
            lpg_result=payload.get("lpg_result"),
            rdf_result=payload.get("rdf_result"),
        )


@dataclass(slots=True)
class DebateRunResponse(JsonSerializable):
    response: str
    trace_steps: List[Dict[str, Any]] = field(default_factory=list)
    debate_results: List[Dict[str, Any]] = field(default_factory=list)
    agent_statuses: List[Dict[str, str]] = field(default_factory=list)
    debate_state: str = "ready"
    degraded: bool = False

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "DebateRunResponse":
        return cls(
            response=str(payload.get("response", "")),
            trace_steps=list(payload.get("trace_steps", [])),
            debate_results=list(payload.get("debate_results", [])),
            agent_statuses=list(payload.get("agent_statuses", [])),
            debate_state=str(payload.get("debate_state", "ready")),
            degraded=bool(payload.get("degraded", False)),
        )


@dataclass(slots=True)
class PlatformTurn(JsonSerializable):
    role: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "PlatformTurn":
        return cls(
            role=str(payload.get("role", "")),
            content=str(payload.get("content", "")),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class PlatformChatResponse(JsonSerializable):
    session_id: str
    mode: str
    assistant_message: str
    trace_steps: List[Dict[str, Any]] = field(default_factory=list)
    ui_payload: Dict[str, Any] = field(default_factory=dict)
    runtime_payload: Dict[str, Any] = field(default_factory=dict)
    history: List[PlatformTurn] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "PlatformChatResponse":
        return cls(
            session_id=str(payload.get("session_id", "")),
            mode=str(payload.get("mode", "")),
            assistant_message=str(payload.get("assistant_message", "")),
            trace_steps=list(payload.get("trace_steps", [])),
            ui_payload=dict(payload.get("ui_payload", {})),
            runtime_payload=dict(payload.get("runtime_payload", {})),
            history=[PlatformTurn.from_dict(item) for item in payload.get("history", [])],
        )


@dataclass(slots=True)
class PlatformSessionResponse(JsonSerializable):
    session_id: str
    history: List[PlatformTurn] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "PlatformSessionResponse":
        return cls(
            session_id=str(payload.get("session_id", "")),
            history=[PlatformTurn.from_dict(item) for item in payload.get("history", [])],
        )


@dataclass(slots=True)
class RawIngestWarning(JsonSerializable):
    record_id: str
    warning_type: str
    message: str

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RawIngestWarning":
        return cls(
            record_id=str(payload.get("record_id", "")),
            warning_type=str(payload.get("warning_type", "")),
            message=str(payload.get("message", "")),
        )


@dataclass(slots=True)
class RawIngestError(JsonSerializable):
    record_id: str
    error_type: str
    message: str

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RawIngestError":
        return cls(
            record_id=str(payload.get("record_id", "")),
            error_type=str(payload.get("error_type", "")),
            message=str(payload.get("message", "")),
        )


@dataclass(slots=True)
class RawIngestResult(JsonSerializable):
    workspace_id: str
    target_database: str
    records_received: int
    records_processed: int
    records_failed: int
    total_nodes: int
    total_relationships: int
    fallback_records: int = 0
    rule_profile: Optional[Dict[str, Any]] = None
    semantic_artifacts: Optional[Dict[str, Any]] = None
    status: str = ""
    warnings: List[RawIngestWarning] = field(default_factory=list)
    errors: List[RawIngestError] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RawIngestResult":
        return cls(
            workspace_id=str(payload.get("workspace_id", "")),
            target_database=str(payload.get("target_database", "")),
            records_received=int(payload.get("records_received", 0)),
            records_processed=int(payload.get("records_processed", 0)),
            records_failed=int(payload.get("records_failed", 0)),
            total_nodes=int(payload.get("total_nodes", 0)),
            total_relationships=int(payload.get("total_relationships", 0)),
            fallback_records=int(payload.get("fallback_records", 0)),
            rule_profile=dict(payload.get("rule_profile", {})) if payload.get("rule_profile") is not None else None,
            semantic_artifacts=(
                dict(payload.get("semantic_artifacts", {}))
                if payload.get("semantic_artifacts") is not None
                else None
            ),
            status=str(payload.get("status", "")),
            warnings=[RawIngestWarning.from_dict(item) for item in payload.get("warnings", [])],
            errors=[RawIngestError.from_dict(item) for item in payload.get("errors", [])],
        )


@dataclass(slots=True)
class FulltextIndexResult(JsonSerializable):
    database: str
    index_name: str
    exists: bool
    created: bool
    state: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    properties: List[str] = field(default_factory=list)
    message: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FulltextIndexResult":
        return cls(
            database=str(payload.get("database", "")),
            index_name=str(payload.get("index_name", "")),
            exists=bool(payload.get("exists", False)),
            created=bool(payload.get("created", False)),
            state=str(payload.get("state", "")).strip() or None,
            labels=[str(item) for item in payload.get("labels", [])],
            properties=[str(item) for item in payload.get("properties", [])],
            message=str(payload.get("message", "")),
        )


@dataclass(slots=True)
class FulltextIndexResponse(JsonSerializable):
    results: List[FulltextIndexResult] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FulltextIndexResponse":
        return cls(results=[FulltextIndexResult.from_dict(item) for item in payload.get("results", [])])
