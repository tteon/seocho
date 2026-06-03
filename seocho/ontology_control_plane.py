from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .models import JsonSerializable


def _clean_text(value: Any, default: str = "") -> str:
    text = str(value if value is not None else "").strip()
    return text or default


def _clean_list(values: Any) -> List[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        key = text.casefold()
        if text and key not in seen:
            out.append(text)
            seen.add(key)
    return out


def _tokenize(text: str) -> set[str]:
    token = ""
    out: set[str] = set()
    for char in text.casefold():
        if char.isalnum():
            token += char
        elif token:
            out.add(token)
            token = ""
    if token:
        out.add(token)
    return out


def _metric(payload: Mapping[str, Any], key: str) -> Optional[float]:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class OntologySignal(JsonSerializable):
    """A query-side or indexing-side event that can improve an ontology profile."""

    source: str
    kind: str
    workspace_id: str = "default"
    profile_id: str = ""
    canonical: str = ""
    observed: str = ""
    confidence: float = 0.0
    evidence_count: int = 1
    affected_queries: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    signal_id: str = ""
    created_at: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OntologySignal":
        return cls(
            source=_clean_text(payload.get("source"), "query"),
            kind=_clean_text(payload.get("kind"), "unknown"),
            workspace_id=_clean_text(payload.get("workspace_id"), "default"),
            profile_id=_clean_text(payload.get("profile_id")),
            canonical=_clean_text(payload.get("canonical")),
            observed=_clean_text(payload.get("observed")),
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            evidence_count=max(1, int(payload.get("evidence_count", 1) or 1)),
            affected_queries=_clean_list(payload.get("affected_queries")),
            metadata=dict(payload.get("metadata", {})) if isinstance(payload.get("metadata"), dict) else {},
            signal_id=_clean_text(payload.get("signal_id")),
            created_at=_clean_text(payload.get("created_at")),
        )


@dataclass(slots=True)
class OntologyProfile(JsonSerializable):
    """Versioned user-reviewable ontology profile used by the control plane."""

    profile_id: str
    workspace_id: str = "default"
    ontology_id: str = ""
    version: str = "draft"
    status: str = "draft"
    ontology_candidate: Dict[str, Any] = field(default_factory=dict)
    vocabulary_candidate: Dict[str, Any] = field(default_factory=dict)
    shacl_candidate: Dict[str, Any] = field(default_factory=dict)
    route_hints: Dict[str, Any] = field(default_factory=dict)
    answer_shapes: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    source_signal_ids: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    promoted_at: str = ""
    promoted_by: str = ""
    promotion_note: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OntologyProfile":
        metrics: Dict[str, float] = {}
        if isinstance(payload.get("metrics"), dict):
            for key, value in payload["metrics"].items():
                try:
                    metrics[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue
        return cls(
            profile_id=_clean_text(payload.get("profile_id") or payload.get("id")),
            workspace_id=_clean_text(payload.get("workspace_id"), "default"),
            ontology_id=_clean_text(payload.get("ontology_id")),
            version=_clean_text(payload.get("version"), "draft"),
            status=_clean_text(payload.get("status"), "draft"),
            ontology_candidate=dict(payload.get("ontology_candidate", {}))
            if isinstance(payload.get("ontology_candidate"), dict)
            else {},
            vocabulary_candidate=dict(payload.get("vocabulary_candidate", {}))
            if isinstance(payload.get("vocabulary_candidate"), dict)
            else {},
            shacl_candidate=dict(payload.get("shacl_candidate", {}))
            if isinstance(payload.get("shacl_candidate"), dict)
            else {},
            route_hints=dict(payload.get("route_hints", {})) if isinstance(payload.get("route_hints"), dict) else {},
            answer_shapes=dict(payload.get("answer_shapes", {}))
            if isinstance(payload.get("answer_shapes"), dict)
            else {},
            metrics=metrics,
            source_signal_ids=_clean_list(payload.get("source_signal_ids")),
            tags=_clean_list(payload.get("tags")),
            created_at=_clean_text(payload.get("created_at")),
            updated_at=_clean_text(payload.get("updated_at")),
            promoted_at=_clean_text(payload.get("promoted_at")),
            promoted_by=_clean_text(payload.get("promoted_by")),
            promotion_note=_clean_text(payload.get("promotion_note")),
        )

    def stable_id(self) -> str:
        source = "|".join(
            [
                self.workspace_id,
                self.profile_id,
                self.ontology_id,
                self.version,
                str(len(self.ontology_candidate.get("classes", []))),
                str(len(self.ontology_candidate.get("relationships", []))),
            ]
        )
        return sha256(source.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class CompiledOntologyProfile(JsonSerializable):
    """Hot-path artifact injected into routing, text2cypher, debate, and answers."""

    schema_version: str = "ontology_control_profile.v1"
    profile_id: str = ""
    workspace_id: str = "default"
    ontology_id: str = ""
    version: str = ""
    status: str = "draft"
    label_aliases: Dict[str, str] = field(default_factory=dict)
    relation_aliases: Dict[str, str] = field(default_factory=dict)
    required_slots: List[str] = field(default_factory=list)
    route_hints: Dict[str, Any] = field(default_factory=dict)
    answer_shapes: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class OntologyProfileSelection(JsonSerializable):
    profile_id: str
    score: float
    reasons: List[str] = field(default_factory=list)
    compiled_profile: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OntologyProfileEvaluation(JsonSerializable):
    profile_id: str
    baseline_profile_id: str = ""
    decision: str = "needs_review"
    expected_effect: Dict[str, float] = field(default_factory=dict)
    metric_deltas: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    user_controls: List[str] = field(default_factory=list)


class OntologyProfileRegistry:
    """Small in-memory registry; persistent stores can wrap the same contract."""

    def __init__(self, profiles: Optional[Iterable[OntologyProfile]] = None) -> None:
        self._profiles: Dict[str, OntologyProfile] = {}
        for profile in profiles or []:
            self.register(profile)

    def register(self, profile: OntologyProfile) -> OntologyProfile:
        if not profile.profile_id:
            raise ValueError("profile_id is required")
        key = self._key(profile.workspace_id, profile.profile_id)
        self._profiles[key] = profile
        return profile

    def get(self, profile_id: str, *, workspace_id: str = "default") -> Optional[OntologyProfile]:
        return self._profiles.get(self._key(workspace_id, profile_id))

    def list(self, *, workspace_id: str = "default", status: str = "") -> List[OntologyProfile]:
        out = [
            profile
            for profile in self._profiles.values()
            if profile.workspace_id == workspace_id and (not status or profile.status == status)
        ]
        return sorted(out, key=lambda item: (item.status != "approved", item.profile_id))

    def promote(self, profile_id: str, *, workspace_id: str = "default") -> OntologyProfile:
        profile = self.get(profile_id, workspace_id=workspace_id)
        if profile is None:
            raise KeyError(profile_id)
        profile.status = "approved"
        return profile

    @staticmethod
    def _key(workspace_id: str, profile_id: str) -> str:
        return f"{workspace_id}:{profile_id}"


class OntologyControlPlane:
    """Selects, compiles, and evaluates ontology profiles between agents and graph stores."""

    def __init__(self, registry: Optional[OntologyProfileRegistry] = None) -> None:
        self.registry = registry or OntologyProfileRegistry()
        self._signals: List[OntologySignal] = []

    def collect_signal(self, signal: OntologySignal | Mapping[str, Any]) -> str:
        item = signal if isinstance(signal, OntologySignal) else OntologySignal.from_dict(signal)
        self._signals.append(item)
        source = "|".join(
            [
                item.workspace_id,
                item.source,
                item.kind,
                item.profile_id,
                item.canonical,
                item.observed,
                str(len(self._signals)),
            ]
        )
        return sha256(source.encode("utf-8")).hexdigest()[:16]

    def signals(
        self,
        *,
        workspace_id: str = "default",
        source: str = "",
        kind: str = "",
    ) -> List[OntologySignal]:
        return [
            signal
            for signal in self._signals
            if signal.workspace_id == workspace_id
            and (not source or signal.source == source)
            and (not kind or signal.kind == kind)
        ]

    def compile_profile(self, profile: OntologyProfile | str, *, workspace_id: str = "default") -> CompiledOntologyProfile:
        item = self._resolve_profile(profile, workspace_id=workspace_id)
        label_aliases, relation_aliases = _compile_aliases(item)
        required_slots = _compile_required_slots(item)
        return CompiledOntologyProfile(
            profile_id=item.profile_id,
            workspace_id=item.workspace_id,
            ontology_id=item.ontology_id,
            version=item.version,
            status=item.status,
            label_aliases=label_aliases,
            relation_aliases=relation_aliases,
            required_slots=required_slots,
            route_hints=dict(item.route_hints),
            answer_shapes=dict(item.answer_shapes),
            metrics=dict(item.metrics),
        )

    def select_profile(
        self,
        question: str,
        *,
        workspace_id: str = "default",
        route_profile: Optional[Mapping[str, Any]] = None,
        include_drafts: bool = False,
    ) -> OntologyProfileSelection:
        statuses = ("approved", "draft") if include_drafts else ("approved",)
        profiles: List[OntologyProfile] = []
        for status in statuses:
            profiles.extend(self.registry.list(workspace_id=workspace_id, status=status))
        if not profiles:
            return OntologyProfileSelection(profile_id="", score=0.0, reasons=["no_profile"])

        scored = [
            self._score_profile(profile, question, route_profile=route_profile or {})
            for profile in profiles
        ]
        scored.sort(key=lambda item: item.score, reverse=True)
        best = scored[0]
        best.compiled_profile = self.compile_profile(best.profile_id, workspace_id=workspace_id).to_dict()
        return best

    def evaluate_profile(
        self,
        candidate: OntologyProfile | str,
        *,
        baseline: Optional[OntologyProfile | str] = None,
        workspace_id: str = "default",
        promote_threshold: float = 0.02,
        reject_threshold: float = -0.02,
    ) -> OntologyProfileEvaluation:
        cand = self._resolve_profile(candidate, workspace_id=workspace_id)
        base = self._resolve_profile(baseline, workspace_id=workspace_id) if baseline is not None else None
        deltas: Dict[str, float] = {}
        if base is not None:
            for key in sorted(set(cand.metrics) | set(base.metrics)):
                cval = _metric(cand.metrics, key)
                bval = _metric(base.metrics, key)
                if cval is not None and bval is not None:
                    deltas[key] = round(cval - bval, 6)

        quality_delta = _quality_delta(deltas)
        cost_delta = deltas.get("token_cost", deltas.get("tokens", 0.0))
        latency_delta = deltas.get("latency_ms", 0.0)
        decision = "needs_review"
        reasons: List[str] = []
        if quality_delta >= promote_threshold and cost_delta <= 0 and latency_delta <= 0:
            decision = "promote_candidate"
            reasons.append("quality_lift")
        elif quality_delta <= reject_threshold:
            decision = "reject_candidate"
            reasons.append("quality_regression")
        else:
            reasons.append("insufficient_lift")
        if latency_delta > 0:
            reasons.append("latency_increase")
        if cost_delta > 0:
            reasons.append("cost_increase")

        controls = [
            "approve_profile",
            "rollback_profile",
            "edit_aliases",
            "edit_required_slots",
            "rerun_regression",
        ]
        return OntologyProfileEvaluation(
            profile_id=cand.profile_id,
            baseline_profile_id=base.profile_id if base is not None else "",
            decision=decision,
            expected_effect={
                "quality_delta": round(quality_delta, 6),
                "latency_ms_delta": round(latency_delta, 6),
                "cost_delta": round(cost_delta, 6),
            },
            metric_deltas=deltas,
            reasons=reasons,
            user_controls=controls,
        )

    def _resolve_profile(self, profile: OntologyProfile | str | None, *, workspace_id: str) -> OntologyProfile:
        if isinstance(profile, OntologyProfile):
            return profile
        if not profile:
            raise ValueError("profile is required")
        item = self.registry.get(str(profile), workspace_id=workspace_id)
        if item is None:
            raise KeyError(str(profile))
        return item

    def _score_profile(
        self,
        profile: OntologyProfile,
        question: str,
        *,
        route_profile: Mapping[str, Any],
    ) -> OntologyProfileSelection:
        question_tokens = _tokenize(question)
        compiled = self.compile_profile(profile)
        score = 0.0
        reasons: List[str] = []

        alias_terms = set(compiled.label_aliases) | set(compiled.relation_aliases)
        alias_hits = sorted(term for term in alias_terms if _tokenize(term) & question_tokens)
        if alias_hits:
            score += min(0.45, 0.08 * len(alias_hits))
            reasons.append("alias_match")

        route_class = _clean_text(route_profile.get("route_class"))
        if route_class and route_class in {str(item) for item in profile.route_hints.get("route_classes", [])}:
            score += 0.2
            reasons.append("route_class_match")

        coverage = _metric(profile.metrics, "slot_coverage")
        if coverage is not None:
            score += max(0.0, min(0.25, coverage * 0.25))
            reasons.append("slot_coverage_metric")

        if profile.status == "approved":
            score += 0.1
            reasons.append("approved_profile")

        for signal in self.signals(workspace_id=profile.workspace_id):
            if signal.profile_id and signal.profile_id != profile.profile_id:
                continue
            if _tokenize(signal.observed or signal.canonical) & question_tokens:
                score += min(0.1, max(0.0, signal.confidence) * 0.05)
                reasons.append(f"{signal.source}_signal")

        return OntologyProfileSelection(
            profile_id=profile.profile_id,
            score=round(score, 6),
            reasons=sorted(set(reasons)),
        )


def _compile_aliases(profile: OntologyProfile) -> tuple[Dict[str, str], Dict[str, str]]:
    label_aliases: Dict[str, str] = {}
    relation_aliases: Dict[str, str] = {}

    for cls in profile.ontology_candidate.get("classes", []):
        if not isinstance(cls, dict):
            continue
        name = _clean_text(cls.get("name"))
        if not name:
            continue
        for alias in [name, *_clean_list(cls.get("aliases"))]:
            label_aliases[alias.casefold()] = name
        for prop in cls.get("properties", []):
            if isinstance(prop, dict):
                prop_name = _clean_text(prop.get("name"))
                for alias in [prop_name, *_clean_list(prop.get("aliases"))]:
                    if alias:
                        label_aliases[alias.casefold()] = name

    for rel in profile.ontology_candidate.get("relationships", []):
        if not isinstance(rel, dict):
            continue
        rel_type = _clean_text(rel.get("type"))
        if not rel_type:
            continue
        for alias in [rel_type, *_clean_list(rel.get("aliases"))]:
            relation_aliases[alias.casefold()] = rel_type

    for term in profile.vocabulary_candidate.get("terms", []):
        if not isinstance(term, dict):
            continue
        canonical = _clean_text(term.get("pref_label") or term.get("name"))
        if not canonical:
            continue
        aliases = [canonical, *_clean_list(term.get("alt_labels") or term.get("aliases"))]
        for alias in aliases:
            label_aliases.setdefault(alias.casefold(), canonical)

    return label_aliases, relation_aliases


def _compile_required_slots(profile: OntologyProfile) -> List[str]:
    slots: List[str] = []
    seen: set[str] = set()
    for shape in profile.shacl_candidate.get("shapes", []):
        if not isinstance(shape, dict):
            continue
        target = _clean_text(shape.get("target_class"))
        for prop in shape.get("properties", []):
            if not isinstance(prop, dict):
                continue
            if _clean_text(prop.get("constraint")) != "minCount":
                continue
            path = _clean_text(prop.get("path"))
            slot = f"{target}.{path}" if target and path else path
            key = slot.casefold()
            if slot and key not in seen:
                slots.append(slot)
                seen.add(key)
    return slots


def _quality_delta(deltas: Mapping[str, float]) -> float:
    for key in ("judge_score", "accuracy", "m1", "slot_coverage", "answer_support"):
        if key in deltas:
            return float(deltas[key])
    return 0.0


__all__ = [
    "CompiledOntologyProfile",
    "OntologyControlPlane",
    "OntologyProfile",
    "OntologyProfileEvaluation",
    "OntologyProfileRegistry",
    "OntologyProfileSelection",
    "OntologySignal",
]
