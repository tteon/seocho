from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, Literal, Mapping, Sequence, Tuple


PolicyVerdict = Literal["allow", "warn", "block"]


def _clean_str(value: Any, default: str = "") -> str:
    candidate = str(value or "").strip()
    return candidate or default


def _str_tuple(values: Iterable[Any] | None) -> Tuple[str, ...]:
    if values is None:
        return ()
    seen: Dict[str, None] = {}
    for value in values:
        item = _clean_str(value)
        if item:
            seen.setdefault(item, None)
    return tuple(seen.keys())


def _dict(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    return dict(value or {})


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _field(payload: Any, name: str, default: Any = "") -> Any:
    if isinstance(payload, Mapping):
        return payload.get(name, default)
    return getattr(payload, name, default)


def _one_or_mixed(values: Tuple[str, ...]) -> str:
    if len(values) == 1:
        return values[0]
    if values:
        return "mixed"
    return ""


@dataclass(frozen=True, slots=True)
class OntologyPolicyDecision:
    """Policy decision attached to an ontology-governed agent/tool run."""

    decision: PolicyVerdict = "allow"
    reason: str = "ok"
    action: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision in {"allow", "warn"}

    @property
    def blocked(self) -> bool:
        return self.decision == "block"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "action": self.action,
            "metadata": dict(self.metadata),
            "allowed": self.allowed,
            "blocked": self.blocked,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "OntologyPolicyDecision":
        data = dict(payload or {})
        decision = _clean_str(data.get("decision"), "allow").lower()
        if decision not in {"allow", "warn", "block"}:
            decision = "warn"
        return cls(
            decision=decision,  # type: ignore[arg-type]
            reason=_clean_str(data.get("reason"), "ok"),
            action=_clean_str(data.get("action")),
            metadata=_dict(_mapping(data.get("metadata"))),
        )

    @classmethod
    def allow(
        cls,
        reason: str = "ok",
        *,
        action: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> "OntologyPolicyDecision":
        return cls("allow", reason, action, _dict(metadata))

    @classmethod
    def warn(
        cls,
        reason: str,
        *,
        action: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> "OntologyPolicyDecision":
        return cls("warn", reason, action, _dict(metadata))

    @classmethod
    def block(
        cls,
        reason: str,
        *,
        action: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> "OntologyPolicyDecision":
        return cls("block", reason, action, _dict(metadata))


@dataclass(frozen=True, slots=True)
class OntologyEvidenceState:
    """Evidence health summary for an ontology-aware query or answer."""

    intent_id: str = ""
    required_slots: Tuple[str, ...] = ()
    filled_slots: Tuple[str, ...] = ()
    missing_slots: Tuple[str, ...] = ()
    selected_triples: Tuple[Dict[str, Any], ...] = ()
    support_level: str = ""
    abstention_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.missing_slots

    @property
    def selected_triple_count(self) -> int:
        return len(self.selected_triples)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "required_slots": list(self.required_slots),
            "filled_slots": list(self.filled_slots),
            "missing_slots": list(self.missing_slots),
            "selected_triples": [dict(item) for item in self.selected_triples],
            "support_level": self.support_level,
            "abstention_reason": self.abstention_reason,
            "metadata": dict(self.metadata),
            "complete": self.complete,
            "selected_triple_count": self.selected_triple_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "OntologyEvidenceState":
        data = dict(payload or {})
        raw_triples = data.get("selected_triples") or ()
        triples = tuple(dict(item) for item in raw_triples if isinstance(item, Mapping))
        return cls(
            intent_id=_clean_str(data.get("intent_id")),
            required_slots=_str_tuple(data.get("required_slots") or ()),
            filled_slots=_str_tuple(data.get("filled_slots") or ()),
            missing_slots=_str_tuple(data.get("missing_slots") or ()),
            selected_triples=triples,
            support_level=_clean_str(data.get("support_level")),
            abstention_reason=_clean_str(data.get("abstention_reason")),
            metadata=_dict(_mapping(data.get("metadata"))),
        )


@dataclass(frozen=True, slots=True)
class OntologyRunContext:
    """Typed middleware envelope shared by SDK, runtime, agents, and tools."""

    workspace_id: str = "default"
    user_id: str = ""
    agent_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    graph_ids: Tuple[str, ...] = ()
    databases: Tuple[str, ...] = ()
    ontology_id: str = ""
    ontology_profile: str = "default"
    vocabulary_profile: str = ""
    ontology_context_hash: str = ""
    glossary_hash: str = ""
    reasoning_mode: bool = False
    repair_budget: int = 0
    tool_budget: int = 0
    allowed_databases: Tuple[str, ...] = ()
    policy_decision: OntologyPolicyDecision = field(
        default_factory=OntologyPolicyDecision
    )
    ontology_context_mismatch: Dict[str, Any] = field(default_factory=dict)
    evidence_state: OntologyEvidenceState = field(default_factory=OntologyEvidenceState)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.policy_decision.blocked

    def allows_database(self, database: str) -> bool:
        """Return whether the context permits a graph tool to touch database."""

        candidate = _clean_str(database)
        if not candidate:
            return False
        if self.allowed_databases:
            return candidate in self.allowed_databases
        if self.databases:
            return candidate in self.databases
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "graph_ids": list(self.graph_ids),
            "databases": list(self.databases),
            "ontology_id": self.ontology_id,
            "ontology_profile": self.ontology_profile,
            "vocabulary_profile": self.vocabulary_profile,
            "ontology_context_hash": self.ontology_context_hash,
            "glossary_hash": self.glossary_hash,
            "reasoning_mode": self.reasoning_mode,
            "repair_budget": self.repair_budget,
            "tool_budget": self.tool_budget,
            "allowed_databases": list(self.allowed_databases),
            "policy_decision": self.policy_decision.to_dict(),
            "ontology_context_mismatch": dict(self.ontology_context_mismatch),
            "evidence_state": self.evidence_state.to_dict(),
            "metadata": dict(self.metadata),
            "blocked": self.blocked,
        }

    def summary(self) -> Dict[str, Any]:
        """Return compact trace metadata safe for logs and public responses."""

        return {
            "workspace_id": self.workspace_id,
            "graph_ids": list(self.graph_ids),
            "databases": list(self.databases),
            "ontology_id": self.ontology_id,
            "ontology_profile": self.ontology_profile,
            "vocabulary_profile": self.vocabulary_profile,
            "ontology_context_hash": self.ontology_context_hash,
            "glossary_hash": self.glossary_hash,
            "policy_decision": self.policy_decision.decision,
            "ontology_mismatch": bool(
                self.ontology_context_mismatch.get("mismatch", False)
            ),
            "missing_evidence_slots": list(self.evidence_state.missing_slots),
        }

    def with_policy_decision(
        self,
        policy_decision: OntologyPolicyDecision | Mapping[str, Any],
    ) -> "OntologyRunContext":
        decision = (
            policy_decision
            if isinstance(policy_decision, OntologyPolicyDecision)
            else OntologyPolicyDecision.from_dict(policy_decision)
        )
        return replace(self, policy_decision=decision)

    def with_evidence_state(
        self,
        evidence_state: OntologyEvidenceState | Mapping[str, Any],
    ) -> "OntologyRunContext":
        evidence = (
            evidence_state
            if isinstance(evidence_state, OntologyEvidenceState)
            else OntologyEvidenceState.from_dict(evidence_state)
        )
        return replace(self, evidence_state=evidence)

    def with_mismatch(
        self,
        ontology_context_mismatch: Mapping[str, Any] | None,
    ) -> "OntologyRunContext":
        return replace(self, ontology_context_mismatch=_dict(ontology_context_mismatch))

    def with_session_scope(
        self,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> "OntologyRunContext":
        return replace(
            self,
            user_id=self.user_id if user_id is None else _clean_str(user_id),
            agent_id=self.agent_id if agent_id is None else _clean_str(agent_id),
            session_id=(
                self.session_id if session_id is None else _clean_str(session_id)
            ),
            turn_id=self.turn_id if turn_id is None else _clean_str(turn_id),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "OntologyRunContext":
        data = dict(payload or {})
        return cls(
            workspace_id=_clean_str(data.get("workspace_id"), "default"),
            user_id=_clean_str(data.get("user_id")),
            agent_id=_clean_str(data.get("agent_id")),
            session_id=_clean_str(data.get("session_id")),
            turn_id=_clean_str(data.get("turn_id")),
            graph_ids=_str_tuple(data.get("graph_ids") or ()),
            databases=_str_tuple(data.get("databases") or ()),
            ontology_id=_clean_str(data.get("ontology_id")),
            ontology_profile=_clean_str(data.get("ontology_profile"), "default"),
            vocabulary_profile=_clean_str(data.get("vocabulary_profile")),
            ontology_context_hash=_clean_str(data.get("ontology_context_hash")),
            glossary_hash=_clean_str(data.get("glossary_hash")),
            reasoning_mode=bool(data.get("reasoning_mode", False)),
            repair_budget=max(int(data.get("repair_budget", 0) or 0), 0),
            tool_budget=max(int(data.get("tool_budget", 0) or 0), 0),
            allowed_databases=_str_tuple(data.get("allowed_databases") or ()),
            policy_decision=OntologyPolicyDecision.from_dict(
                _mapping(data.get("policy_decision"))
            ),
            ontology_context_mismatch=_dict(
                _mapping(data.get("ontology_context_mismatch"))
            ),
            evidence_state=OntologyEvidenceState.from_dict(
                _mapping(data.get("evidence_state"))
            ),
            metadata=_dict(_mapping(data.get("metadata"))),
        )

    @classmethod
    def from_ontology_context(
        cls,
        ontology_context: Any,
        *,
        workspace_id: str | None = None,
        graph_ids: Sequence[str] | None = None,
        database: str | None = None,
        databases: Sequence[str] | None = None,
        allowed_databases: Sequence[str] | None = None,
        user_id: str = "",
        agent_id: str = "",
        session_id: str = "",
        turn_id: str = "",
        reasoning_mode: bool = False,
        repair_budget: int = 0,
        tool_budget: int = 0,
        policy_decision: OntologyPolicyDecision | Mapping[str, Any] | None = None,
        ontology_context_mismatch: Mapping[str, Any] | None = None,
        evidence_state: OntologyEvidenceState | Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "OntologyRunContext":
        if hasattr(ontology_context, "metadata"):
            payload = dict(ontology_context.metadata(usage="run_context"))
        else:
            payload = dict(ontology_context or {})
        resolved_databases = _str_tuple(databases or ([database] if database else ()))
        resolved_allowed = _str_tuple(allowed_databases or resolved_databases)
        return cls(
            workspace_id=_clean_str(
                workspace_id or payload.get("workspace_id"),
                "default",
            ),
            user_id=_clean_str(user_id),
            agent_id=_clean_str(agent_id),
            session_id=_clean_str(session_id),
            turn_id=_clean_str(turn_id),
            graph_ids=_str_tuple(graph_ids),
            databases=resolved_databases,
            ontology_id=_clean_str(payload.get("ontology_id")),
            ontology_profile=_clean_str(
                payload.get("profile") or payload.get("ontology_profile"),
                "default",
            ),
            vocabulary_profile=_clean_str(
                payload.get("vocabulary_profile") or payload.get("profile"),
                "default",
            ),
            ontology_context_hash=_clean_str(
                payload.get("context_hash") or payload.get("ontology_context_hash")
            ),
            glossary_hash=_clean_str(payload.get("glossary_hash")),
            reasoning_mode=bool(reasoning_mode),
            repair_budget=max(int(repair_budget or 0), 0),
            tool_budget=max(int(tool_budget or 0), 0),
            allowed_databases=resolved_allowed,
            policy_decision=(
                policy_decision
                if isinstance(policy_decision, OntologyPolicyDecision)
                else OntologyPolicyDecision.from_dict(policy_decision)
            ),
            ontology_context_mismatch=_dict(ontology_context_mismatch),
            evidence_state=(
                evidence_state
                if isinstance(evidence_state, OntologyEvidenceState)
                else OntologyEvidenceState.from_dict(evidence_state)
            ),
            metadata=_dict(metadata),
        )

    @classmethod
    def from_runtime_graph_targets(
        cls,
        graph_targets: Iterable[Any],
        *,
        workspace_id: str = "default",
        graph_ids: Sequence[str] | None = None,
        databases: Sequence[str] | None = None,
        allowed_databases: Sequence[str] | None = None,
        ontology_profile: str = "default",
        user_id: str = "",
        agent_id: str = "",
        session_id: str = "",
        turn_id: str = "",
        reasoning_mode: bool = False,
        repair_budget: int = 0,
        tool_budget: int = 0,
        policy_decision: OntologyPolicyDecision | Mapping[str, Any] | None = None,
        ontology_context_mismatch: Mapping[str, Any] | None = None,
        evidence_state: OntologyEvidenceState | Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "OntologyRunContext":
        targets = list(graph_targets)
        target_graph_ids = _str_tuple(
            graph_ids or (_field(item, "graph_id") for item in targets)
        )
        target_databases = _str_tuple(
            databases or (_field(item, "database") for item in targets)
        )
        ontology_ids = _str_tuple(_field(item, "ontology_id") for item in targets)
        vocabulary_profiles = _str_tuple(
            _field(item, "vocabulary_profile") for item in targets
        )
        resolved_allowed = _str_tuple(allowed_databases or target_databases)
        return cls(
            workspace_id=_clean_str(workspace_id, "default"),
            user_id=_clean_str(user_id),
            agent_id=_clean_str(agent_id),
            session_id=_clean_str(session_id),
            turn_id=_clean_str(turn_id),
            graph_ids=target_graph_ids,
            databases=target_databases,
            ontology_id=_one_or_mixed(ontology_ids),
            ontology_profile=_clean_str(ontology_profile, "default"),
            vocabulary_profile=_one_or_mixed(vocabulary_profiles),
            reasoning_mode=bool(reasoning_mode),
            repair_budget=max(int(repair_budget or 0), 0),
            tool_budget=max(int(tool_budget or 0), 0),
            allowed_databases=resolved_allowed,
            policy_decision=(
                policy_decision
                if isinstance(policy_decision, OntologyPolicyDecision)
                else OntologyPolicyDecision.from_dict(policy_decision)
            ),
            ontology_context_mismatch=_dict(ontology_context_mismatch),
            evidence_state=(
                evidence_state
                if isinstance(evidence_state, OntologyEvidenceState)
                else OntologyEvidenceState.from_dict(evidence_state)
            ),
            metadata=_dict(metadata),
        )


def build_local_ontology_run_context(
    ontology_context: Any,
    **kwargs: Any,
) -> OntologyRunContext:
    """Build an `OntologyRunContext` from a compiled local SDK ontology context."""

    return OntologyRunContext.from_ontology_context(ontology_context, **kwargs)


def build_runtime_ontology_run_context(
    graph_targets: Iterable[Any],
    **kwargs: Any,
) -> OntologyRunContext:
    """Build an `OntologyRunContext` from runtime graph target records."""

    return OntologyRunContext.from_runtime_graph_targets(graph_targets, **kwargs)
