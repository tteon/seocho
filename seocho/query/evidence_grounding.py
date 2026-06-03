from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Sequence


GROUNDING_OPTIMIZER_PROFILES: tuple[Dict[str, str], ...] = (
    {
        "agent_id": "professor_agent",
        "lens": "semantic_adequacy",
        "principle": "answers must expose whether the required ontology slots are actually filled",
    },
    {
        "agent_id": "software_engineer_agent",
        "lens": "typed_contract",
        "principle": "generation receives a compact evidence contract, not free-form debate transcripts",
    },
    {
        "agent_id": "computer_systems_agent",
        "lens": "bounded_hot_path",
        "principle": "grounding work must be deterministic, bounded, and reusable in traces",
    },
)

_MAX_RECORDS = 8
_MAX_CONTEXT_FRAGMENTS = 10
_MAX_FRAGMENT_CHARS = 900
_MAX_CONTEXT_CHARS = 6000


def grounding_optimizer_receipt() -> Dict[str, Any]:
    """Return the SEOCHO-specific grounding optimizer receipt.

    The named profiles are intentionally advisory, not autonomous debate agents:
    they encode the design review lenses used to shape the answer contract.
    """

    return {
        "schema_version": "grounding_optimizer.v1",
        "mode": "typed_evidence_to_answer",
        "profiles": [dict(profile) for profile in GROUNDING_OPTIMIZER_PROFILES],
        "expected_effect": "raise answer support by forcing final synthesis to use only typed evidence fragments",
    }


def build_grounded_synthesis_prompt(
    *,
    question: str,
    records: Sequence[Mapping[str, Any]],
    vector_context: str = "",
    evidence_bundle: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a compact evidence-first prompt payload for answer synthesis."""

    bundle = dict(evidence_bundle or {})
    fragments = _record_fragments(records)
    fragments.extend(_context_fragments(vector_context, start_index=len(fragments) + 1))
    fragment_payload = [
        {
            "id": fragment["id"],
            "source": fragment["source"],
            "text": fragment["text"],
        }
        for fragment in fragments
    ]
    missing_slots = _clean_list(bundle.get("missing_slots", []))
    grounded_slots = _clean_list(bundle.get("grounded_slots", []))
    focus_slots = _clean_list(bundle.get("focus_slots", []))
    support_status = str(
        bundle.get("support_status")
        or (bundle.get("support_assessment", {}) or {}).get("status")
        or ""
    ).strip()

    system_addendum = "\n".join(
        [
            "",
            "--- SEOCHO Evidence Grounding Contract ---",
            "Use only the evidence fragments and typed slot fills below.",
            "Do not answer from model memory, unstated financial knowledge, or unstated graph edges.",
            "Preserve exact numbers, units, entity names, and years from the evidence when present.",
            "Treat graph_context_fallback fragments as valid provenance-bearing evidence, not as model memory.",
            "When evidence fragments contain the raw values needed for simple arithmetic, compute deltas, ratios, percentages, and trends explicitly from those values.",
            "If a required slot is missing, answer only the supported part and name the missing slot.",
            "Prefer a short directly supported answer over a broad synthesis.",
        ]
    )
    user_payload = {
        "question": question,
        "typed_slots": {
            "focus_slots": focus_slots,
            "grounded_slots": grounded_slots,
            "missing_slots": missing_slots,
            "slot_fills": bundle.get("slot_fills", {}),
            "support_status": support_status,
        },
        "evidence_fragments": fragment_payload,
        "answer_rules": [
            "Every factual claim in the answer must be supported by at least one evidence fragment.",
            "Do not refuse solely because evidence came from graph_context_fallback instead of structured_record.",
            "For financial questions, use raw values in fragments to calculate requested shares, changes, and trends; label calculated values as calculated from evidence.",
            "If fragments conflict, report the conflict instead of resolving it from prior knowledge.",
            "If no fragment supports the requested answer, say the current graph evidence is insufficient.",
        ],
    }
    return {
        "schema_version": "grounded_synthesis_prompt.v1",
        "system_addendum": system_addendum,
        "user_addendum": "\n\nSEOCHO typed evidence payload:\n"
        + json.dumps(user_payload, ensure_ascii=False, default=str),
        "fragment_count": len(fragment_payload),
        "missing_slots": missing_slots,
        "support_status": support_status,
        "optimizer": grounding_optimizer_receipt(),
    }


def _record_fragments(records: Sequence[Mapping[str, Any]]) -> List[Dict[str, str]]:
    fragments: List[Dict[str, str]] = []
    for index, record in enumerate(records[:_MAX_RECORDS], start=1):
        text = _record_text(record)
        if not text:
            continue
        fragments.append(
            {
                "id": f"E{len(fragments) + 1}",
                "source": "structured_record",
                "text": _truncate(text),
            }
        )
    return fragments


def _record_text(record: Mapping[str, Any]) -> str:
    priority_keys = (
        "supporting_fact",
        "company",
        "metric_name",
        "value",
        "year",
        "source_entity",
        "relation_type",
        "relationship",
        "target_entity",
        "target",
        "properties",
        "neighbors",
    )
    parts: List[str] = []
    for key in priority_keys:
        if key not in record:
            continue
        rendered = _render_value(record.get(key))
        if rendered:
            parts.append(f"{key}={rendered}")
    if not parts:
        for key, value in sorted(record.items(), key=lambda item: str(item[0])):
            rendered = _render_value(value)
            if rendered:
                parts.append(f"{key}={rendered}")
    return "; ".join(parts)


def _context_fragments(vector_context: str, *, start_index: int) -> List[Dict[str, str]]:
    text = str(vector_context or "").strip()
    if not text:
        return []
    text = text[:_MAX_CONTEXT_CHARS]
    raw_parts = [
        part.strip()
        for part in re.split(r"\n{2,}|(?<=\.)\s+(?=\[|[A-Z0-9])", text)
        if part.strip()
    ]
    fragments: List[Dict[str, str]] = []
    for part in raw_parts[:_MAX_CONTEXT_FRAGMENTS]:
        cleaned = re.sub(r"\s+", " ", part).strip()
        if not cleaned or cleaned == "=== Knowledge graph ===":
            continue
        fragments.append(
            {
                "id": f"E{start_index + len(fragments)}",
                "source": "graph_context_fallback",
                "text": _truncate(cleaned),
            }
        )
    return fragments


def _render_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    if isinstance(value, list):
        rendered = [_render_value(item) for item in value[:8]]
        return "[" + ", ".join(item for item in rendered if item) + "]"
    if isinstance(value, dict):
        parts: List[str] = []
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            rendered = _render_value(item)
            if rendered:
                parts.append(f"{key}: {rendered}")
        return "{" + ", ".join(parts) + "}" if parts else ""
    return str(value).strip()


def _truncate(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if len(cleaned) <= _MAX_FRAGMENT_CHARS:
        return cleaned
    return cleaned[:_MAX_FRAGMENT_CHARS].rsplit(" ", 1)[0].rstrip()


def _clean_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
