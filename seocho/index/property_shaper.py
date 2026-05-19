"""Graph-CoT-oriented property shaping for indexed nodes and relationships.

Implements the property schema defined by ADR-0092. Properties are treated as
an agent control surface (claim / useWhen / reasoningRole / confidence /
sourceRefs / embeddingText), not just descriptive metadata.

Thin-slice scope: enforce required fields with safe defaults, validate fixed
enums, compose ``embeddingText`` deterministically, and detect promotion
candidates without emitting them. Persistent index creation (fulltext /
vector / property) is out of scope for this slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


SEMANTIC_ROLE_ENUM: frozenset[str] = frozenset(
    {
        "concept",
        "claim",
        "definition",
        "method",
        "example",
        "risk",
        "metric",
        "source",
        "decision",
        "evidence",
        "constraint",
    }
)


REASONING_ROLE_ENUM: frozenset[str] = frozenset(
    {
        "premise",
        "bridge",
        "constraint",
        "evidence",
        "counterEvidence",
        "hypothesis",
        "answerCandidate",
        "nextStepHint",
        "strategy",
    }
)


RELATIONSHIP_TYPES: frozenset[str] = frozenset(
    {
        "SUPPORTS",
        "CONTRADICTS",
        "REQUIRES",
        "CAUSES",
        "IMPLEMENTS",
        "EXAMPLE_OF",
        "PART_OF",
        "ALTERNATIVE_TO",
        "EVIDENCED_BY",
        "SUPPORTED_BY",
        "MENTIONS",
    }
)


REQUIRED_NODE_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "claim",
    "agentSummary",
    "semanticRole",
    "reasoningRole",
    "answers",
    "useWhen",
    "confidence",
    "sourceRefs",
    "embeddingText",
)


RECOMMENDED_NODE_FIELDS: tuple[str, ...] = (
    "doNotUseWhen",
    "domainScope",
    "validFrom",
    "validTo",
    "importance",
    "graphCotUtility",
    "preferredNextRelations",
    "extractionConfidence",
)


REQUIRED_EDGE_FIELDS: tuple[str, ...] = (
    "relationSummary",
    "reasoningRole",
    "confidence",
    "sourceRefs",
)


_AGENT_SUMMARY_MAX_CHARS = 200
_EMBEDDING_TEXT_LIST_JOIN = " "
_PROMOTE_LIST_LENGTH = 5
_PROMOTE_STRING_LENGTH = 512


@dataclass(frozen=True)
class PromotionRequest:
    """A field value that should become a separate node + relationship."""

    field: str
    reason: str
    payload: Any


class PropertyShaper:
    """Shape node/edge properties per ADR-0092.

    The shaper is deliberately tolerant: missing required fields get safe
    defaults instead of raising, so existing extraction outputs can flow
    through the pipeline once the feature flag is enabled. Enum violations
    raise ``ValueError`` because they would silently corrupt agent prompts.
    """

    def __init__(
        self,
        *,
        default_semantic_role: str = "concept",
        default_reasoning_role: str = "premise",
        default_confidence: float = 0.5,
    ) -> None:
        if default_semantic_role not in SEMANTIC_ROLE_ENUM:
            raise ValueError(
                f"default_semantic_role={default_semantic_role!r} not in "
                f"SEMANTIC_ROLE_ENUM"
            )
        if default_reasoning_role not in REASONING_ROLE_ENUM:
            raise ValueError(
                f"default_reasoning_role={default_reasoning_role!r} not in "
                f"REASONING_ROLE_ENUM"
            )
        self._default_semantic_role = default_semantic_role
        self._default_reasoning_role = default_reasoning_role
        self._default_confidence = float(default_confidence)

    def shape_node(self, raw_props: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of ``raw_props`` with the ADR-0092 required fields.

        Existing keys are preserved. Required keys that are missing are
        filled with safe defaults. Enum-typed keys are validated.
        """
        shaped: Dict[str, Any] = dict(raw_props or {})

        node_id = shaped.get("id") or shaped.get("name") or ""
        if not node_id:
            raise ValueError("shape_node requires either 'id' or 'name' in raw_props")
        shaped["id"] = str(node_id)

        shaped.setdefault("title", shaped.get("name") or shaped["id"])

        claim = shaped.get("claim") or shaped.get("description") or shaped["title"]
        shaped["claim"] = str(claim)

        shaped.setdefault("agentSummary", _truncate(shaped["claim"], _AGENT_SUMMARY_MAX_CHARS))

        semantic_role = shaped.get("semanticRole", self._default_semantic_role)
        if semantic_role not in SEMANTIC_ROLE_ENUM:
            raise ValueError(
                f"semanticRole={semantic_role!r} not in SEMANTIC_ROLE_ENUM"
            )
        shaped["semanticRole"] = semantic_role

        reasoning_role = shaped.get("reasoningRole", self._default_reasoning_role)
        if reasoning_role not in REASONING_ROLE_ENUM:
            raise ValueError(
                f"reasoningRole={reasoning_role!r} not in REASONING_ROLE_ENUM"
            )
        shaped["reasoningRole"] = reasoning_role

        shaped["answers"] = _coerce_str_list(shaped.get("answers"))
        shaped["useWhen"] = _coerce_str_list(shaped.get("useWhen"))

        confidence = shaped.get("confidence", self._default_confidence)
        shaped["confidence"] = _clip_unit(float(confidence))

        shaped["sourceRefs"] = _coerce_str_list(shaped.get("sourceRefs"))

        shaped["embeddingText"] = self.compose_embedding_text(shaped)
        return shaped

    def shape_edge(
        self,
        raw_props: Dict[str, Any],
        *,
        edge_type: str,
    ) -> Dict[str, Any]:
        """Return a copy of ``raw_props`` with required edge fields filled.

        Rejects relationship types that are not in the canonical vocabulary.
        """
        if edge_type not in RELATIONSHIP_TYPES:
            raise ValueError(
                f"edge_type={edge_type!r} not in RELATIONSHIP_TYPES"
            )

        shaped: Dict[str, Any] = dict(raw_props or {})

        shaped.setdefault(
            "relationSummary",
            f"{edge_type.lower().replace('_', ' ')} relation",
        )

        reasoning_role = shaped.get("reasoningRole", self._default_reasoning_role)
        if reasoning_role not in REASONING_ROLE_ENUM:
            raise ValueError(
                f"edge reasoningRole={reasoning_role!r} not in REASONING_ROLE_ENUM"
            )
        shaped["reasoningRole"] = reasoning_role

        confidence = shaped.get("confidence", self._default_confidence)
        shaped["confidence"] = _clip_unit(float(confidence))

        shaped["sourceRefs"] = _coerce_str_list(shaped.get("sourceRefs"))
        return shaped

    @staticmethod
    def compose_embedding_text(node: Dict[str, Any]) -> str:
        """Deterministic ``embeddingText`` composition.

        Concatenates ``title + claim + agentSummary + answers + useWhen``
        with single-space separation. Existing ``embeddingText`` value is
        respected when explicitly provided (non-empty).
        """
        existing = node.get("embeddingText")
        if isinstance(existing, str) and existing.strip():
            return existing.strip()

        parts: List[str] = []
        for field in ("title", "claim", "agentSummary"):
            value = node.get(field)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        for list_field in ("answers", "useWhen"):
            values = _coerce_str_list(node.get(list_field))
            if values:
                parts.append(_EMBEDDING_TEXT_LIST_JOIN.join(values))
        return _EMBEDDING_TEXT_LIST_JOIN.join(parts)

    @staticmethod
    def promotion_candidates(node: Dict[str, Any]) -> List[PromotionRequest]:
        """Detect properties that should be promoted to standalone nodes.

        Thin-slice: report only. Emission of the promoted nodes is out of
        scope and will land in a follow-up slice.
        """
        candidates: List[PromotionRequest] = []
        for field, value in (node or {}).items():
            if isinstance(value, list) and len(value) > _PROMOTE_LIST_LENGTH:
                if field in {"sourceRefs", "assumptions"}:
                    candidates.append(
                        PromotionRequest(
                            field=field,
                            reason=f"list length {len(value)} > {_PROMOTE_LIST_LENGTH}",
                            payload=value,
                        )
                    )
            elif isinstance(value, str) and len(value) > _PROMOTE_STRING_LENGTH:
                if field in {"evidenceText", "assumptions"}:
                    candidates.append(
                        PromotionRequest(
                            field=field,
                            reason=f"string length {len(value)} > {_PROMOTE_STRING_LENGTH}",
                            payload=value,
                        )
                    )
        return candidates


def _truncate(value: str, max_chars: int) -> str:
    text = (value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _coerce_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, Sequence):
        out: List[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return [str(value).strip()] if str(value).strip() else []


def _clip_unit(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)
