"""Runtime entry point for FIBO-aware indexing/query selection.

``run_with_fibo`` is the slice-1 wiring described in issue ``seocho-1dm8``.
Given a workspace, a catalog, a selector, and the request prompt, it
returns a :class:`FIBORunDescriptor` that locks the selected FIBO module
set together with the trace and cache-key metadata required by
``CLAUDE.md`` §6.1 (workspace propagation) and §18 (KV-cache shape).

Heavy reasoning stays out of the hot path (CLAUDE.md §6.3): the selector
reads only the precomputed label index, never the live owlready graph.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping

from ..agent_config import RoutingPolicy
from .catalog import FIBOCatalog
from .selector import (
    FIBOSelector,
    LexicalSelector,
    SelectionPolicy,
    SelectionResult,
    SelectionStatus,
)


AUDIT_REFUSE_THRESHOLD: float = 0.7
"""When ``RoutingPolicy.audit_strictness`` exceeds this, ``NO_MATCH`` is a refusal."""


class RunMode(str, Enum):
    INDEX = "index"
    QUERY = "query"


class FIBOSelectionRefused(Exception):
    """Selection produced no FIBO match while audit strictness required grounding.

    Carries the workspace and policy context so the caller can surface the
    refusal in traces without falling back silently
    (``feedback_explicit_interfaces``).
    """

    def __init__(
        self,
        *,
        workspace_id: str,
        rationale: str,
        audit_strictness: float,
        confidence: float,
    ) -> None:
        super().__init__(
            f"FIBO selection refused under audit_strictness={audit_strictness:.2f}: "
            f"{rationale}"
        )
        self.workspace_id = workspace_id
        self.rationale = rationale
        self.audit_strictness = audit_strictness
        self.confidence = confidence


@dataclass(frozen=True, slots=True)
class FIBORunDescriptor:
    """Locked FIBO identity for one indexing or query run.

    The descriptor travels with the request: it produces the trace
    metadata fields (``to_trace_metadata``) and contributes a stable
    fragment to the KV-cache key (``cache_key_fragment``). Two runs with
    the same descriptor identity must reach the same cache slot.
    """

    workspace_id: str
    fibo_version: str
    modules: tuple[str, ...]
    selector_name: str
    selection_status: SelectionStatus
    selection_confidence: float
    candidate_iris: tuple[str, ...]
    rationale: str
    mode: RunMode
    cache_key_fragment: str
    per_module_score: Mapping[str, float] = field(default_factory=dict)

    def to_trace_metadata(self) -> Dict[str, Any]:
        """Trace span fields per ticket scope (4) and CLAUDE.md §9."""

        return {
            "fibo_modules": list(self.modules),
            "fibo_version": self.fibo_version,
            "selector_kind": self.selector_name,
            "selection_confidence": self.selection_confidence,
            "selection_status": self.selection_status.value,
            "candidate_iris": list(self.candidate_iris),
            "workspace_id": self.workspace_id,
            "mode": self.mode.value,
            "cache_key_fragment": self.cache_key_fragment,
        }


def _stable_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _cache_key_fragment(
    *,
    workspace_id: str,
    fibo_version: str,
    modules: tuple[str, ...],
) -> str:
    """Stable 16-char hash of the (workspace, fibo_version, modules) triple.

    Modules are sorted to make the fragment order-insensitive. Combine
    with a prompt hash at the call site to form the full KV-cache key
    (CLAUDE.md §18).
    """

    payload = {
        "workspace_id": workspace_id,
        "fibo_version": fibo_version,
        "modules": sorted(modules),
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:16]


def _resolve_selection_policy(
    policy: RoutingPolicy | SelectionPolicy | None,
) -> tuple[SelectionPolicy, float]:
    """Return ``(selection_policy, audit_strictness)``.

    Accepts either a runtime ``RoutingPolicy`` (full policy surface) or a
    bare ``SelectionPolicy`` (test/standalone callers).
    """

    if policy is None:
        sel = SelectionPolicy()
        return sel, sel.audit_strictness
    if isinstance(policy, RoutingPolicy):
        sel = policy.to_selection_policy()
        return sel, policy.audit_strictness
    if isinstance(policy, SelectionPolicy):
        return policy, policy.audit_strictness
    raise TypeError(
        f"unsupported policy type: {type(policy).__name__}; "
        "expected RoutingPolicy or SelectionPolicy"
    )


def run_with_fibo(
    *,
    prompt: str,
    workspace_id: str,
    catalog: FIBOCatalog,
    mode: RunMode,
    selector: FIBOSelector | None = None,
    policy: RoutingPolicy | SelectionPolicy | None = None,
) -> FIBORunDescriptor:
    """Select FIBO modules for one run and return the locked descriptor.

    Failure semantics (slice-1 contract):

    - ``SelectionStatus.OK`` — descriptor carries the expanded module set.
    - ``SelectionStatus.LOW_CONFIDENCE`` — descriptor returned with
      empty ``modules``; caller decides whether to widen coverage. The
      candidate IRIs are surfaced for debugging.
    - ``SelectionStatus.NO_MATCH`` and ``audit_strictness >= 0.7`` —
      raises :class:`FIBOSelectionRefused`. No silent root-class fallback.
    - ``SelectionStatus.NO_MATCH`` and ``audit_strictness < 0.7`` —
      descriptor returned with empty ``modules`` so the caller can degrade.

    ``workspace_id`` is empty-string-validated to keep the §6.1 contract
    explicit; an empty workspace_id is rejected.
    """

    if not workspace_id or not workspace_id.strip():
        raise ValueError("workspace_id is required (CLAUDE.md §6.1)")

    selector = selector or LexicalSelector()
    selection_policy, audit_strictness = _resolve_selection_policy(policy)
    result: SelectionResult = selector.select(
        prompt, catalog=catalog, policy=selection_policy
    )

    if (
        result.status is SelectionStatus.NO_MATCH
        and audit_strictness >= AUDIT_REFUSE_THRESHOLD
    ):
        raise FIBOSelectionRefused(
            workspace_id=workspace_id,
            rationale=result.rationale,
            audit_strictness=audit_strictness,
            confidence=result.confidence,
        )

    fragment = _cache_key_fragment(
        workspace_id=workspace_id,
        fibo_version=catalog.fibo_version,
        modules=result.modules,
    )

    return FIBORunDescriptor(
        workspace_id=workspace_id,
        fibo_version=catalog.fibo_version,
        modules=result.modules,
        selector_name=getattr(selector, "name", selector.__class__.__name__),
        selection_status=result.status,
        selection_confidence=result.confidence,
        candidate_iris=result.candidate_iris,
        rationale=result.rationale,
        mode=mode,
        cache_key_fragment=fragment,
        per_module_score=dict(result.per_module_score),
    )
