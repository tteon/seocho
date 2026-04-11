from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence


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
class GraphRef(JsonSerializable):
    graph_id: str
    database: Optional[str] = None
    ontology_id: Optional[str] = None
    vocabulary_profile: Optional[str] = None
    description: str = ""
    workspace_scope: str = "default"

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "GraphRef":
        return cls(
            graph_id=str(payload.get("graph_id", "")).strip(),
            database=str(payload.get("database", "")).strip() or None,
            ontology_id=str(payload.get("ontology_id", "")).strip() or None,
            vocabulary_profile=str(payload.get("vocabulary_profile", "")).strip() or None,
            description=str(payload.get("description", "")),
            workspace_scope=str(payload.get("workspace_scope", "default") or "default"),
        )

    @classmethod
    def from_graph_target(cls, payload: "GraphTarget | Dict[str, Any]") -> "GraphRef":
        if isinstance(payload, GraphTarget):
            return cls(
                graph_id=payload.graph_id,
                database=payload.database,
                ontology_id=payload.ontology_id,
                vocabulary_profile=payload.vocabulary_profile,
                description=payload.description,
                workspace_scope=payload.workspace_scope,
            )
        return cls.from_dict(payload)


@dataclass(slots=True)
class ReasoningPolicy(JsonSerializable):
    style: Literal["direct", "react", "debate"] = "direct"
    max_steps: Optional[int] = None
    tool_budget: Optional[int] = None
    require_grounded_evidence: bool = True
    repair_budget: int = 0
    fallback_style: Optional[Literal["direct", "react", "debate"]] = None

    def normalized_style(self) -> Literal["direct", "react", "debate"]:
        normalized = str(self.style or "direct").strip().lower()
        if normalized not in {"direct", "react", "debate"}:
            raise ValueError(
                f"Unsupported reasoning style '{self.style}'. "
                "Expected one of: direct, react, debate."
            )
        return normalized  # type: ignore[return-value]


@dataclass(slots=True)
class ExecutionPlan(JsonSerializable):
    query: str
    targets: List[GraphRef] = field(default_factory=list)
    reasoning: ReasoningPolicy = field(default_factory=ReasoningPolicy)
    entity_overrides: List["EntityOverride"] = field(default_factory=list)
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    workspace_id: Optional[str] = None
    ontology_ids: List[str] = field(default_factory=list)
    vocabulary_profiles: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ExecutionPlan":
        reasoning_payload = payload.get("reasoning", {})
        entity_override_payload = payload.get("entity_overrides", [])
        return cls(
            query=str(payload.get("query", "")),
            targets=[
                GraphRef.from_dict(item)
                for item in payload.get("targets", [])
                if isinstance(item, dict)
            ],
            reasoning=(
                reasoning_payload
                if isinstance(reasoning_payload, ReasoningPolicy)
                else ReasoningPolicy(**reasoning_payload)
                if isinstance(reasoning_payload, dict)
                else ReasoningPolicy()
            ),
            entity_overrides=[
                item
                if isinstance(item, EntityOverride)
                else EntityOverride.from_dict(item)
                for item in entity_override_payload
                if isinstance(item, (dict, EntityOverride))
            ],
            user_id=str(payload.get("user_id", "")).strip() or None,
            session_id=str(payload.get("session_id", "")).strip() or None,
            workspace_id=str(payload.get("workspace_id", "")).strip() or None,
            ontology_ids=[
                str(item).strip()
                for item in payload.get("ontology_ids", [])
                if str(item).strip()
            ],
            vocabulary_profiles=[
                str(item).strip()
                for item in payload.get("vocabulary_profiles", [])
                if str(item).strip()
            ],
        )

    @property
    def graph_ids(self) -> List[str]:
        return [target.graph_id for target in self.targets if target.graph_id]

    @property
    def databases(self) -> List[str]:
        return [target.database for target in self.targets if target.database]


@dataclass(slots=True)
class ExecutionResult(JsonSerializable):
    requested_style: Literal["direct", "react", "debate"]
    runtime_mode: Literal["semantic", "router", "debate"]
    response: str
    resolved_targets: List[GraphRef] = field(default_factory=list)
    graph_ids: List[str] = field(default_factory=list)
    databases: List[str] = field(default_factory=list)
    trace_steps: List[Dict[str, Any]] = field(default_factory=list)
    router_result: Optional["AgentRunResponse"] = None
    semantic_result: Optional["SemanticRunResponse"] = None
    debate_result: Optional["DebateRunResponse"] = None

    def _delegated_result(
        self,
    ) -> Optional["AgentRunResponse | SemanticRunResponse | DebateRunResponse"]:
        if self.runtime_mode == "semantic":
            return self.semantic_result
        if self.runtime_mode == "debate":
            return self.debate_result
        if self.runtime_mode == "router":
            return self.router_result
        return None

    def __getattr__(self, name: str) -> Any:
        delegated = self._delegated_result()
        if delegated is not None and hasattr(delegated, name):
            return getattr(delegated, name)
        raise AttributeError(f"{type(self).__name__!s} object has no attribute {name!r}")

    @classmethod
    def from_run_result(
        cls,
        *,
        requested_style: Literal["direct", "react", "debate"],
        runtime_mode: Literal["semantic", "router", "debate"],
        resolved_targets: Sequence[GraphRef],
        result: "AgentRunResponse | SemanticRunResponse | DebateRunResponse",
    ) -> "ExecutionResult":
        graph_ids = [target.graph_id for target in resolved_targets if target.graph_id]
        databases = [target.database for target in resolved_targets if target.database]
        payload = cls(
            requested_style=requested_style,
            runtime_mode=runtime_mode,
            response=str(getattr(result, "response", "")),
            resolved_targets=list(resolved_targets),
            graph_ids=graph_ids,
            databases=databases,
            trace_steps=list(getattr(result, "trace_steps", [])),
        )
        if runtime_mode == "semantic" and isinstance(result, SemanticRunResponse):
            payload.semantic_result = result
        elif runtime_mode == "debate" and isinstance(result, DebateRunResponse):
            payload.debate_result = result
        elif runtime_mode == "router" and isinstance(result, AgentRunResponse):
            payload.router_result = result
        return payload


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
