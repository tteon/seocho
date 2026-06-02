from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Sequence

import yaml

from .agent_config import AgentConfig, RoutingPolicy


_SUPPORTED_PATTERNS = {
    "planning_multi_agent",
    "reflection_chain",
    "memory_tool_use",
}
_ALLOWED_EXTRACTION_STRATEGIES = {"general", "domain", "multi_pass"}
_ALLOWED_LINKING_STRATEGIES = {"llm", "embedding", "none"}
_ALLOWED_VALIDATION_MODES = {"reject", "retry", "relax", "warn"}
_ALLOWED_QUERY_STRATEGIES = {"llm_cypher", "template", "hybrid"}
_ALLOWED_ANSWER_STYLES = {"concise", "evidence", "table"}
_ALLOWED_EXECUTION_MODES = {"pipeline", "agent", "supervisor"}

_PATTERN_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "planning_multi_agent": {
        "execution_mode": "supervisor",
        "handoff": True,
        "reasoning_mode": True,
        "repair_budget": 3,
        "query_strategy": "template",
        "answer_style": "evidence",
        "extraction_strategy": "domain",
        "linking_strategy": "llm",
        "validation_on_fail": "retry",
        "routing_policy": "thorough",
    },
    "reflection_chain": {
        "execution_mode": "agent",
        "handoff": False,
        "reasoning_mode": True,
        "repair_budget": 3,
        "query_strategy": "template",
        "answer_style": "evidence",
        "extraction_strategy": "domain",
        "linking_strategy": "llm",
        "validation_on_fail": "retry",
        "routing_policy": "balanced",
    },
    "memory_tool_use": {
        "execution_mode": "agent",
        "handoff": False,
        "reasoning_mode": True,
        "repair_budget": 2,
        "query_strategy": "template",
        "answer_style": "concise",
        "extraction_strategy": "general",
        "linking_strategy": "embedding",
        "validation_on_fail": "warn",
        "routing_policy": "balanced",
    },
}


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _dict(value: Any, *, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping.")
    return dict(value)


def _routing_policy(value: str) -> RoutingPolicy | None:
    normalized = _string(value).lower()
    if not normalized:
        return None
    if normalized == "fast":
        return RoutingPolicy.fast()
    if normalized == "balanced":
        return RoutingPolicy.balanced()
    if normalized == "thorough":
        return RoutingPolicy.thorough()
    raise ValueError(
        "routing_policy must be one of: fast, balanced, thorough."
    )


@dataclass(slots=True)
class OntologyBinding:
    """Declarative ontology binding required by an agent design spec."""

    required: bool = True
    profile: str = ""
    ontology_id: str = ""
    package_id: str = ""
    path: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OntologyBinding":
        return cls(
            required=bool(payload.get("required", True)),
            profile=_string(payload.get("profile")),
            ontology_id=_string(payload.get("ontology_id")),
            package_id=_string(payload.get("package_id")),
            path=_string(payload.get("path")),
        )

    def validate(self) -> None:
        if not self.required:
            return
        if any((self.profile, self.ontology_id, self.package_id, self.path)):
            return
        raise ValueError(
            "Agent design specs must declare an ontology binding. "
            "Add ontology.profile, ontology_id, package_id, or path."
        )

    def resolved_profile(self) -> str:
        return self.profile or "default"


@dataclass(slots=True)
class AgentDesignSpec:
    """YAML-declared agent pattern that compiles into AgentConfig."""

    name: str
    pattern: str
    ontology: OntologyBinding
    description: str = ""
    agent: Dict[str, Any] = field(default_factory=dict)
    indexing: Dict[str, Any] = field(default_factory=dict)
    query: Dict[str, Any] = field(default_factory=dict)
    reasoning_cycle: Dict[str, Any] = field(default_factory=dict)
    memory: Dict[str, Any] = field(default_factory=dict)
    tools: Sequence[str] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentDesignSpec":
        if not isinstance(payload, Mapping):
            raise ValueError("Agent design spec must be a mapping.")
        if "ontology" not in payload:
            raise ValueError("Agent design spec requires an 'ontology' section.")

        spec = cls(
            name=_string(payload.get("name")),
            pattern=_string(payload.get("pattern")).lower(),
            description=_string(payload.get("description")),
            ontology=OntologyBinding.from_dict(_dict(payload.get("ontology"), field_name="ontology")),
            agent=_dict(payload.get("agent"), field_name="agent"),
            indexing=_dict(payload.get("indexing"), field_name="indexing"),
            query=_dict(payload.get("query"), field_name="query"),
            reasoning_cycle=_dict(payload.get("reasoning_cycle"), field_name="reasoning_cycle"),
            memory=_dict(payload.get("memory"), field_name="memory"),
            tools=tuple(str(item).strip() for item in payload.get("tools", []) or [] if str(item).strip()),
            metadata=_dict(payload.get("metadata"), field_name="metadata"),
        )
        spec.validate()
        return spec

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AgentDesignSpec":
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        return cls.from_dict(payload)

    def validate(self) -> None:
        if not self.name:
            raise ValueError("Agent design spec requires a non-empty name.")
        if self.pattern not in _SUPPORTED_PATTERNS:
            raise ValueError(
                "Unsupported agent pattern "
                f"{self.pattern!r}. Known patterns: {', '.join(sorted(_SUPPORTED_PATTERNS))}."
            )
        self.ontology.validate()

        for section, key, allowed in (
            (self.indexing, "extraction_strategy", _ALLOWED_EXTRACTION_STRATEGIES),
            (self.indexing, "linking_strategy", _ALLOWED_LINKING_STRATEGIES),
            (self.indexing, "validation_on_fail", _ALLOWED_VALIDATION_MODES),
            (self.query, "query_strategy", _ALLOWED_QUERY_STRATEGIES),
            (self.query, "answer_style", _ALLOWED_ANSWER_STYLES),
            (self.agent, "execution_mode", _ALLOWED_EXECUTION_MODES),
        ):
            value = _string(section.get(key))
            if value and value not in allowed:
                raise ValueError(
                    f"{key} must be one of: {', '.join(sorted(allowed))}."
                )

        if self.reasoning_cycle and not isinstance(self.reasoning_cycle, dict):
            raise ValueError("reasoning_cycle must be a mapping.")
        enabled = self.reasoning_cycle.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("reasoning_cycle.enabled must be a boolean.")
        anomaly_sources = self.reasoning_cycle.get("anomaly_sources")
        if anomaly_sources is not None:
            if not isinstance(anomaly_sources, list) or not all(
                isinstance(item, str) and item.strip() for item in anomaly_sources
            ):
                raise ValueError("reasoning_cycle.anomaly_sources must be a list of strings.")
        for section_name in ("abduction", "deduction", "induction", "promotion"):
            value = self.reasoning_cycle.get(section_name)
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"reasoning_cycle.{section_name} must be a mapping.")

        _routing_policy(_string(self.agent.get("routing_policy")))

    def to_agent_config(self) -> AgentConfig:
        payload: Dict[str, Any] = dict(_PATTERN_DEFAULTS[self.pattern])

        for key in (
            "execution_mode",
            "handoff",
            "reasoning_mode",
            "repair_budget",
        ):
            if key in self.agent:
                payload[key] = self.agent[key]

        for key in ("routing_policy",):
            if key in self.agent:
                payload[key] = self.agent[key]

        for key in (
            "extraction_strategy",
            "extraction_quality_threshold",
            "extraction_retry_on_low_quality",
            "linking_strategy",
            "validation_on_fail",
        ):
            if key in self.indexing:
                payload[key] = self.indexing[key]

        for key in ("query_strategy", "answer_style", "reasoning_mode", "repair_budget"):
            if key in self.query:
                payload[key] = self.query[key]

        config_payload: MutableMapping[str, Any] = {
            "execution_mode": _string(payload.get("execution_mode")) or "pipeline",
            "handoff": bool(payload.get("handoff", False)),
            "reasoning_mode": bool(payload.get("reasoning_mode", False)),
            "repair_budget": int(payload.get("repair_budget", 2)),
            "query_strategy": _string(payload.get("query_strategy")) or "llm_cypher",
            "answer_style": _string(payload.get("answer_style")) or "concise",
            "extraction_strategy": _string(payload.get("extraction_strategy")) or "general",
            "extraction_quality_threshold": float(payload.get("extraction_quality_threshold", 0.0)),
            "extraction_retry_on_low_quality": bool(payload.get("extraction_retry_on_low_quality", False)),
            "linking_strategy": _string(payload.get("linking_strategy")) or "llm",
            "validation_on_fail": _string(payload.get("validation_on_fail")) or "warn",
            "routing_policy": _routing_policy(_string(payload.get("routing_policy"))),
            "extra": {
                "agent_design_name": self.name,
                "agent_design_pattern": self.pattern,
                "agent_design_tools": list(self.tools),
                "agent_design_memory": dict(self.memory),
                "agent_design_metadata": dict(self.metadata),
                "agent_design_reasoning_cycle": dict(self.reasoning_cycle),
                "agent_design_ontology": {
                    "required": self.ontology.required,
                    "profile": self.ontology.resolved_profile(),
                    "ontology_id": self.ontology.ontology_id,
                    "package_id": self.ontology.package_id,
                    "path": self.ontology.path,
                },
            },
        }
        return AgentConfig(**config_payload)

    def client_kwargs(self) -> Dict[str, Any]:
        return {
            "agent_config": self.to_agent_config(),
            "ontology_profile": self.ontology.resolved_profile(),
        }


def load_agent_design_spec(path: str | Path) -> AgentDesignSpec:
    """Load and validate a YAML agent design spec."""

    return AgentDesignSpec.from_yaml(path)


__all__ = [
    "AgentDesignSpec",
    "OntologyBinding",
    "load_agent_design_spec",
]
