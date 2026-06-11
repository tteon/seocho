"""Adversarial extraction critique — recall/precision diagnostic (GRL Artefact 2).

GRL "AI-Assisted Ontology Engineering" (KGC 2026) makes adversarial critique
non-negotiable: LLMs are sycophantic and will not surface their own weaknesses in
direct review. For *ontology authoring* GRL runs four perspectives; for
*extraction output* the high-value perspectives collapse to two orthogonal,
decorrelated axes (run as independent passes, per GRL's "separate fresh
conversation" guidance):

  - RECALL adversary  — what grounded facts/entities/figures/relationships in the
                        SOURCE are ABSENT from the extracted graph? (the measured
                        bottleneck: graph quality is recall-gated.)
  - PRECISION adversary — what nodes/edges in the extraction are NOT supported by
                        the source, or are mislabeled (wrong ontology class)?

This is a DIAGNOSTIC, distinct from ``finder_judge`` (which scores the final
answer, downstream of the graph). It explains *why* the graph was thin. Its
output is **never auto-applied** to the graph — doing so would change the
independent variable and void the §20 comparison. It is offline, env-gated
(``SEOCHO_ONTOLOGY_CRITIQUE``, default OFF), and the LLM is injected (MARA-first
via ``seocho.store.llm.create_llm_backend``; a fake backend makes it testable at
$0).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


RECALL_ADVERSARY_SYSTEM = (
    "You are a skeptical knowledge-graph extraction auditor. You are graded on the "
    "weaknesses you find, not on collegiality. Given SOURCE TEXT and the EXTRACTED "
    "graph (nodes + relationships) drawn from it, list every grounded fact, entity, "
    "figure, or relationship that is STATED in the source but ABSENT from the "
    "extraction. For each, quote the exact source span and name the ontology class "
    "or relationship type it should have used. Do NOT invent anything not in the "
    "source. Assume the extractor was lazy and dropped real evidence. "
    'Respond ONLY as JSON: {"missed": [{"span": "<quote>", "suggested_label": '
    '"<Class|REL_TYPE>", "why": "<short>"}]}'
)

PRECISION_ADVERSARY_SYSTEM = (
    "You are a skeptical knowledge-graph extraction auditor. You are graded on the "
    "weaknesses you find, not on collegiality. Given SOURCE TEXT and the EXTRACTED "
    "graph, list (a) every node or relationship NOT supported by the source "
    "(hallucinated) and (b) every node assigned the WRONG ontology class given the "
    "source. Quote the contradicting or missing span for each. Do NOT propose "
    "additions. "
    'Respond ONLY as JSON: {"hallucinated": [{"item": "<node/edge>", "why": '
    '"<short>"}], "mislabeled": [{"item": "<node>", "got": "<Class>", "expected": '
    '"<Class>", "why": "<short>"}]}'
)


class _LLMLike(Protocol):
    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> Any: ...


@dataclass(slots=True)
class CritiqueResult:
    enabled: bool
    missed: List[Dict[str, Any]] = field(default_factory=list)
    hallucinated: List[Dict[str, Any]] = field(default_factory=list)
    mislabeled: List[Dict[str, Any]] = field(default_factory=list)
    extracted_node_count: int = 0
    recall_proxy: float = 0.0          # missed / (missed + extracted) — 0..1, higher = worse recall
    precision_proxy: float = 1.0       # supported / extracted — 0..1, higher = better precision
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "missed": list(self.missed),
            "hallucinated": list(self.hallucinated),
            "mislabeled": list(self.mislabeled),
            "extracted_node_count": self.extracted_node_count,
            "recall_proxy": round(self.recall_proxy, 4),
            "precision_proxy": round(self.precision_proxy, 4),
            "errors": list(self.errors),
        }


def is_enabled(explicit: Optional[bool] = None) -> bool:
    """Critique is OFF unless explicitly enabled or SEOCHO_ONTOLOGY_CRITIQUE is set."""
    if explicit is not None:
        return explicit
    return os.environ.get("SEOCHO_ONTOLOGY_CRITIQUE", "").strip().lower() in {"1", "true", "yes", "on"}


def _serialize_extraction(extracted: Dict[str, Any]) -> str:
    nodes = extracted.get("nodes", []) or []
    rels = extracted.get("relationships", []) or extracted.get("edges", []) or []
    lines = ["NODES:"]
    for n in nodes:
        label = n.get("label") or n.get("type") or "?"
        props = n.get("properties", {}) or {}
        lines.append(f"- ({label}) {json.dumps(props, ensure_ascii=False, sort_keys=True)}")
    lines.append("RELATIONSHIPS:")
    for r in rels:
        lines.append(f"- ({r.get('source','?')})-[:{r.get('type','?')}]->({r.get('target','?')})")
    return "\n".join(lines)


def build_recall_prompt(source_text: str, extracted: Dict[str, Any]) -> str:
    return f"SOURCE TEXT:\n{source_text}\n\nEXTRACTED:\n{_serialize_extraction(extracted)}"


def build_precision_prompt(source_text: str, extracted: Dict[str, Any]) -> str:
    return f"SOURCE TEXT:\n{source_text}\n\nEXTRACTED:\n{_serialize_extraction(extracted)}"


def _parse_json_block(text: str) -> Dict[str, Any]:
    """Tolerant JSON extraction from a possibly-chatty LLM reply."""
    if not text:
        return {}
    fence = re.search(r"\{.*\}", text, re.DOTALL)
    raw = fence.group(0) if fence else text
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _complete_text(llm: _LLMLike, system: str, user: str) -> str:
    try:
        resp = llm.complete(system=system, user=user, temperature=0.0)
    except TypeError:
        resp = llm.complete(system=system, user=user)
    return getattr(resp, "text", None) or getattr(resp, "content", None) or str(resp)


def critique_extraction(
    source_text: str,
    extracted: Dict[str, Any],
    *,
    recall_llm: _LLMLike,
    precision_llm: Optional[_LLMLike] = None,
    enabled: Optional[bool] = None,
) -> CritiqueResult:
    """Run the two decorrelated adversaries over one extraction; return a
    DIAGNOSTIC (never auto-applied). ``precision_llm`` defaults to ``recall_llm``
    (use distinct instances/conversations for sharper decorrelation per GRL).

    ``recall_proxy`` = missed / (missed + extracted_nodes): an offline proxy for
    the extraction recall gate, correlatable with downstream slice accuracy.
    """
    if not is_enabled(enabled):
        return CritiqueResult(enabled=False)

    precision_llm = precision_llm or recall_llm
    node_count = len(extracted.get("nodes", []) or [])
    errors: List[str] = []

    missed: List[Dict[str, Any]] = []
    try:
        recall_raw = _parse_json_block(
            _complete_text(recall_llm, RECALL_ADVERSARY_SYSTEM, build_recall_prompt(source_text, extracted))
        )
        missed = [m for m in (recall_raw.get("missed") or []) if isinstance(m, dict)]
    except Exception as exc:  # diagnostic must never crash the sweep
        errors.append(f"recall_adversary: {exc}")

    hallucinated: List[Dict[str, Any]] = []
    mislabeled: List[Dict[str, Any]] = []
    try:
        prec_raw = _parse_json_block(
            _complete_text(precision_llm, PRECISION_ADVERSARY_SYSTEM, build_precision_prompt(source_text, extracted))
        )
        hallucinated = [h for h in (prec_raw.get("hallucinated") or []) if isinstance(h, dict)]
        mislabeled = [m for m in (prec_raw.get("mislabeled") or []) if isinstance(m, dict)]
    except Exception as exc:
        errors.append(f"precision_adversary: {exc}")

    denom = len(missed) + node_count
    recall_proxy = (len(missed) / denom) if denom else 0.0
    unsupported = len(hallucinated) + len(mislabeled)
    precision_proxy = ((node_count - min(unsupported, node_count)) / node_count) if node_count else 1.0

    return CritiqueResult(
        enabled=True,
        missed=missed,
        hallucinated=hallucinated,
        mislabeled=mislabeled,
        extracted_node_count=node_count,
        recall_proxy=recall_proxy,
        precision_proxy=precision_proxy,
        errors=errors,
    )
