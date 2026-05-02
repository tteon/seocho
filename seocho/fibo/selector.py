from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol

from .catalog import FIBOCatalog


class SelectionStatus(str, Enum):
    OK = "ok"
    LOW_CONFIDENCE = "low_confidence"
    NO_MATCH = "no_match"


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    """Selector-local policy surface.

    ``RoutingPolicy.to_selection_policy()`` derives this from the runtime
    routing policy. The ``audit_strictness`` threshold is read by the
    caller (``seocho.fibo.runtime.run_with_fibo``), not enforced here —
    the selector returns ``SelectionStatus`` and the caller decides
    whether to refuse.
    """

    min_confidence: float = 0.1
    audit_strictness: float = 0.5
    max_candidates: int = 5


@dataclass(frozen=True, slots=True)
class SelectionResult:
    modules: tuple[str, ...]
    confidence: float
    rationale: str
    candidate_iris: tuple[str, ...]
    status: SelectionStatus
    per_module_score: Mapping[str, float] = field(default_factory=dict)


class FIBOSelector(Protocol):
    name: str

    def select(
        self,
        text: str,
        *,
        catalog: FIBOCatalog,
        policy: SelectionPolicy,
    ) -> SelectionResult: ...


_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def _tokenize(text: str) -> frozenset[str]:
    return frozenset(m.group(0).lower() for m in _TOKEN.finditer(text))


def _label_tokens(label: str) -> tuple[str, ...]:
    return tuple(t.lower() for t in _TOKEN.findall(label))


def _saturating_score(hits: int, *, scale: int = 5) -> float:
    """Map hit count to a (0, 1) score that saturates near 1 above ``scale``.

    The shape is ``hits / (hits + scale)`` so 1 hit → 0.17, 5 hits → 0.5,
    20 hits → 0.8. Independent of module size, which is intentional —
    large FIBO modules should not be penalized for breadth.
    """

    if hits <= 0:
        return 0.0
    return hits / (hits + scale)


@dataclass(frozen=True, slots=True)
class LexicalSelector:
    """Deterministic FIBO module selector based on label hits.

    A label matches when every token in the label's lowercased form is
    present in the tokenized input. Module score is a saturating function
    of unique-IRI hits; modules above ``policy.min_confidence`` are
    selected and expanded with their declared ``depends_on`` modules.

    Output order is alphabetic by module code so callers can rely on
    stable cache keys (CLAUDE.md §18 KV-cache contract).
    """

    name: str = "lexical"
    score_scale: int = 5

    def select(
        self,
        text: str,
        *,
        catalog: FIBOCatalog,
        policy: SelectionPolicy,
    ) -> SelectionResult:
        tokens = _tokenize(text)
        per_module: dict[str, float] = {}
        per_module_iris: dict[str, list[tuple[str, str]]] = {}

        for code, module in catalog.modules.items():
            hits: list[tuple[str, str]] = []
            for label, iri in module.label_index.items():
                label_toks = _label_tokens(label)
                if not label_toks:
                    continue
                if all(tok in tokens for tok in label_toks):
                    hits.append((label, iri))
            if hits:
                hits.sort(key=lambda pair: (pair[0], pair[1]))
                per_module_iris[code] = hits
                per_module[code] = _saturating_score(
                    len(hits), scale=self.score_scale
                )

        if not per_module:
            return SelectionResult(
                modules=(),
                confidence=0.0,
                rationale="no FIBO label matched the input",
                candidate_iris=(),
                status=SelectionStatus.NO_MATCH,
                per_module_score={},
            )

        selected_codes = sorted(
            code
            for code, score in per_module.items()
            if score >= policy.min_confidence
        )
        if not selected_codes:
            best_code = max(per_module, key=lambda c: (per_module[c], c))
            best_score = per_module[best_code]
            top_iris = tuple(
                iri for _, iri in per_module_iris[best_code][: policy.max_candidates]
            )
            return SelectionResult(
                modules=(),
                confidence=best_score,
                rationale=(
                    f"best module {best_code} scored {best_score:.2f} below "
                    f"min_confidence {policy.min_confidence:.2f}"
                ),
                candidate_iris=top_iris,
                status=SelectionStatus.LOW_CONFIDENCE,
                per_module_score=dict(per_module),
            )

        expanded = catalog.with_dependencies(selected_codes)
        confidence = max(per_module[c] for c in selected_codes)

        candidate_iris: list[str] = []
        seen_iris: set[str] = set()
        for code in selected_codes:
            for _, iri in per_module_iris[code]:
                if iri in seen_iris:
                    continue
                seen_iris.add(iri)
                candidate_iris.append(iri)
                if len(candidate_iris) >= policy.max_candidates:
                    break
            if len(candidate_iris) >= policy.max_candidates:
                break

        rationale_parts = [
            f"{code}={per_module[code]:.2f}" for code in selected_codes
        ]
        rationale = "matched " + ", ".join(rationale_parts)

        return SelectionResult(
            modules=expanded,
            confidence=confidence,
            rationale=rationale,
            candidate_iris=tuple(candidate_iris),
            status=SelectionStatus.OK,
            per_module_score=dict(per_module),
        )
