from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from ..runtime_contract import DEFAULT_QUERY_MODE, normalize_query_mode
from .evidence_grounding import grounding_optimizer_receipt
from .evidence_swarm import build_evidence_swarm_report


def build_local_query_metadata(
    *,
    workspace_id: str,
    agent_design_pattern: str,
    question: str,
    database: str,
    ontology: Any,
    ontology_context: Any,
    ontology_context_mismatch: Dict[str, Any],
    cypher: str,
    params: Dict[str, Any],
    intent_data: Optional[Dict[str, Any]],
    records: Sequence[Dict[str, Any]],
    answer_text: str,
    attempts: Sequence[Dict[str, Any]],
    repair_budget: int,
    latency_breakdown_ms: Dict[str, float],
    vector_context: str,
    error: str,
    answer_source: str,
    query_mode: str = DEFAULT_QUERY_MODE,
) -> Dict[str, Any]:
    """Build the local SDK query observability contract.

    The shape intentionally mirrors runtime semantic responses so benchmark and
    trace consumers can compare SDK and runtime behavior without path-specific
    adapters.
    """

    query_mode = normalize_query_mode(query_mode)
    normalized_breakdown = _roll_up_latency(latency_breakdown_ms)
    support_assessment = _local_support_assessment(
        records=records,
        answer_text=answer_text,
        vector_context=vector_context,
        error=error,
    )
    evidence_bundle = _local_evidence_bundle(
        question=question,
        database=database,
        intent_data=intent_data or {},
        records=records,
        vector_context=vector_context,
        support_assessment=support_assessment,
    )
    support_assessment = dict(evidence_bundle.get("support_assessment", support_assessment))
    agent_pattern = _local_agent_pattern_receipt(
        configured_pattern=agent_design_pattern,
        answer_source=answer_source,
        reasoning_attempts=len(attempts),
        repair_budget=repair_budget,
        query_mode=query_mode,
        support_assessment=support_assessment,
    )
    token_usage = _estimate_local_token_usage(
        question=question,
        answer_text=answer_text,
        cypher=cypher,
        records=records,
        answer_source=answer_source,
    )
    diagnostics = _query_diagnostics(
        records=records,
        vector_context=vector_context,
        error=error,
    )
    answer_envelope = {
        "schema_version": "answer_envelope.v1",
        "answer": answer_text,
        "answer_source": answer_source,
        "query_mode": query_mode,
        "support_assessment": support_assessment,
        "evidence_bundle": evidence_bundle,
        "grounding_optimizer": grounding_optimizer_receipt(),
        "query_diagnostics": diagnostics,
        "latency_breakdown_ms": normalized_breakdown,
        "token_usage": token_usage,
        "agent_pattern": agent_pattern,
    }

    return {
        "schema_version": "query_run_metadata.v1",
        "workspace_id": workspace_id,
        "database": database,
        "query_mode": query_mode,
        "ontology_context": ontology_context.metadata(usage="query"),
        "ontology_context_mismatch": ontology_context_mismatch,
        "ontology_name": getattr(ontology, "name", ""),
        "cypher": cypher,
        "params": dict(params or {}),
        "intent_data": dict(intent_data or {}),
        "result_count": len(records or []),
        "reasoning_attempts": len(attempts),
        "latency_breakdown_ms": normalized_breakdown,
        "support_assessment": support_assessment,
        "evidence_bundle": evidence_bundle,
        "grounding_optimizer": grounding_optimizer_receipt(),
        "query_diagnostics": diagnostics,
        "token_usage": token_usage,
        "agent_pattern": agent_pattern,
        "answer_envelope": answer_envelope,
    }


def build_local_evidence_bundle_for_synthesis(
    *,
    question: str,
    database: str,
    intent_data: Optional[Dict[str, Any]],
    records: Sequence[Dict[str, Any]],
    vector_context: str,
    answer_text: str = "",
    error: str = "",
) -> Dict[str, Any]:
    """Build the local evidence contract before answer generation."""

    support_assessment = _local_support_assessment(
        records=records,
        answer_text=answer_text,
        vector_context=vector_context,
        error=error,
    )
    return _local_evidence_bundle(
        question=question,
        database=database,
        intent_data=intent_data or {},
        records=records,
        vector_context=vector_context,
        support_assessment=support_assessment,
    )


def _roll_up_latency(latency_breakdown_ms: Dict[str, float]) -> Dict[str, float]:
    retrieval_keys = (
        "schema_ms",
        "plan_ms",
        "execute_ms",
        "neighbor_fallback_ms",
        "repair_ms",
        "vector_ms",
        "ontology_context_check_ms",
    )
    retrieval_ms = round(
        sum(float(latency_breakdown_ms.get(key, 0.0) or 0.0) for key in retrieval_keys),
        2,
    )
    generation_ms = round(
        float(latency_breakdown_ms.get("generation_ms", 0.0) or 0.0)
        + float(latency_breakdown_ms.get("deterministic_answer_ms", 0.0) or 0.0),
        2,
    )
    payload = dict(latency_breakdown_ms)
    payload["retrieval_ms"] = retrieval_ms
    payload["generation_ms"] = generation_ms
    return payload


def _local_support_assessment(
    *,
    records: Sequence[Dict[str, Any]],
    answer_text: str,
    vector_context: str,
    error: str,
) -> Dict[str, Any]:
    if error:
        return {
            "status": "unsupported",
            "reason": "query_error",
            "row_count": len(records or []),
            "missing_slots": ["query_execution"],
        }
    if records:
        return {
            "status": "supported" if answer_text else "partial",
            "reason": "graph_records_returned",
            "row_count": len(records),
            "missing_slots": [] if answer_text else ["answer"],
            "supported": bool(answer_text),
        }
    if vector_context:
        derivation = _derived_support_receipt(answer_text, vector_context)
        if derivation:
            return {
                "status": "derived_supported",
                "support_class": "derived_supported",
                "reason": "evidence_backed_derivation_from_graph_context",
                "row_count": 0,
                "missing_slots": [],
                "supported": True,
                "evidence_source": "graph_context_fallback",
                "derivation": derivation,
            }
        return {
            "status": "partial",
            "reason": "vector_context_only",
            "row_count": 0,
            "missing_slots": ["graph_records"],
            "supported": False,
        }
    return {
        "status": "unsupported",
        "reason": "no_graph_records",
        "row_count": 0,
        "missing_slots": ["evidence"],
        "supported": False,
    }


def _local_evidence_bundle(
    *,
    question: str,
    database: str,
    intent_data: Dict[str, Any],
    records: Sequence[Dict[str, Any]],
    vector_context: str,
    support_assessment: Dict[str, Any],
) -> Dict[str, Any]:
    intent = str(intent_data.get("intent", "") or "entity_summary")
    focus_slots = _focus_slots_for_local_intent(intent)
    slot_fills: Dict[str, Any] = {}
    anchor = str(intent_data.get("anchor_entity", "") or "").strip()
    target = str(intent_data.get("target_entity", "") or "").strip()
    metric = str(intent_data.get("metric_name", "") or "").strip()
    years = [str(year) for year in intent_data.get("years", []) if str(year).strip()]
    relationship = str(intent_data.get("relationship_type", "") or "").strip()

    if anchor:
        slot_fills["source_entity" if target else "target_entity"] = anchor
    if target:
        slot_fills["target_entity"] = target
    if metric:
        slot_fills["financial_metric"] = metric
    if years:
        slot_fills["period"] = years
    if relationship:
        slot_fills["relation_paths"] = [relationship]

    supporting_fact = _first_supporting_fact(records)
    if not supporting_fact and vector_context:
        supporting_fact = _context_excerpt(vector_context)
    if supporting_fact:
        slot_fills["supporting_fact"] = supporting_fact

    if "financial_metric" in focus_slots and "financial_metric" not in slot_fills:
        inferred_metric = _financial_metric_from_question(question)
        if inferred_metric:
            slot_fills["financial_metric"] = inferred_metric

    if "period" in focus_slots and "period" not in slot_fills:
        inferred_years = _years_from_text(" ".join([supporting_fact, vector_context, question]))
        if inferred_years:
            slot_fills["period"] = inferred_years

    grounded_slots = [slot for slot in focus_slots if slot in slot_fills]
    missing_slots = [slot for slot in focus_slots if slot not in slot_fills]
    coverage = round(len(grounded_slots) / max(1, len(focus_slots)), 4) if focus_slots else 1.0
    support_assessment = _calibrate_support_for_required_slots(
        support_assessment=support_assessment,
        missing_slots=missing_slots,
        coverage=coverage,
    )
    provenance = [
        {
            "database": database,
            "record_index": index,
            "source": "structured_record",
            "keys": sorted(str(key) for key in record.keys()),
        }
        for index, record in enumerate(records[:3])
    ]
    if vector_context:
        provenance.append(
            {
                "database": database,
                "source": "graph_context_fallback",
                "context_chars": len(vector_context),
            }
        )

    bundle = {
        "schema_version": "evidence_bundle.v2",
        "intent_id": intent,
        "database": database,
        "focus_slots": focus_slots,
        "slot_fills": slot_fills,
        "grounded_slots": grounded_slots,
        "missing_slots": missing_slots,
        "provenance": provenance,
        "confidence": 1.0 if records else 0.0,
        "coverage": coverage,
        "support_assessment": dict(support_assessment),
    }
    bundle["evidence_swarm"] = build_evidence_swarm_report(
        question=question,
        semantic_context={
            "entities": [value for value in (anchor, target) if value],
            "query_diagnostics": _query_diagnostics(
                records=records,
                vector_context="",
                error="",
            ),
        },
        evidence_bundle=bundle,
        support_assessment=support_assessment,
    )
    return bundle


def _focus_slots_for_local_intent(intent: str) -> List[str]:
    if intent in {"financial_metric_lookup", "financial_metric_delta"}:
        return ["target_entity", "financial_metric", "period", "supporting_fact"]
    if intent == "relationship_lookup":
        return ["source_entity", "target_entity", "relation_paths", "supporting_fact"]
    if intent == "engineering_tradeoff_lookup":
        return ["target_entity", "limitation_points", "alternative_points", "supporting_fact"]
    return ["target_entity", "supporting_fact"]


def _first_supporting_fact(records: Sequence[Dict[str, Any]]) -> str:
    for record in records:
        for key in ("supporting_fact", "content", "text"):
            value = record.get(key) if isinstance(record, dict) else None
            if value:
                return str(value).strip()
    return ""


def _context_excerpt(vector_context: str) -> str:
    text = " ".join(str(vector_context or "").split())
    if len(text) <= 1200:
        return text
    return text[:1200].rsplit(" ", 1)[0].rstrip()


def _years_from_text(text: str) -> List[str]:
    years: List[str] = []
    for match in re.finditer(r"\b(?:19|20)\d{2}\b", str(text or "")):
        year = match.group(0)
        if year not in years:
            years.append(year)
    return years[:8]


def _financial_metric_from_question(question: str) -> str:
    text = str(question or "").casefold()
    candidates = (
        ("revenue growth", ("revenue growth", "rev growth")),
        ("revenue", ("revenue", "rev")),
        ("operating performance", ("operating perf", "operating performance")),
        ("operating efficiency", ("op eff", "operating efficiency")),
        ("operating margin", ("operating margin", "ebitda margin")),
        ("free cash flow", ("free cash flow", "fcf")),
        ("return on invested capital", ("return on invested capital", "roic")),
        ("supply chain", ("supply chain", "lead-time", "lead time")),
        ("demand forecast", ("demand forecast", "long-term demand")),
        ("drug development services", ("drug dev svc", "drug development")),
        ("technology integration", ("tech int", "technology integration")),
        ("financial metrics", ("fin metrics", "financial metrics")),
    )
    found: List[str] = []
    for metric, aliases in candidates:
        if any(alias in text for alias in aliases) and metric not in found:
            found.append(metric)
    return ", ".join(found[:6])


def _calibrate_support_for_required_slots(
    *,
    support_assessment: Dict[str, Any],
    missing_slots: Sequence[str],
    coverage: float,
) -> Dict[str, Any]:
    support = dict(support_assessment or {})
    missing = [str(slot) for slot in missing_slots if str(slot).strip()]
    if not missing:
        support.setdefault("supported", str(support.get("status", "")).lower() in {"supported", "derived_supported"})
        support["missing_slots"] = []
        support["coverage"] = coverage
        return support

    status = str(support.get("status", "") or "").strip()
    if status in {"supported", "derived_supported"} or bool(support.get("supported")):
        previous_status = status or ("supported" if support.get("supported") else "")
        support["previous_status"] = previous_status
        support["status"] = "partial"
        support["support_class"] = "partial"
        support["reason"] = "partial_required_slots_missing"
        support["supported"] = False
    support["missing_slots"] = missing
    support["coverage"] = coverage
    derivation = support.get("derivation")
    if isinstance(derivation, dict):
        warnings = list(derivation.get("warnings") or [])
        warning = f"required_slots_missing_after_derivation:{','.join(missing)}"
        if warning not in warnings:
            warnings.append(warning)
        derivation["warnings"] = warnings
        support["derivation"] = derivation
    return support


def _derived_support_receipt(answer_text: str, vector_context: str) -> Optional[Dict[str, Any]]:
    answer = str(answer_text or "").strip()
    context = str(vector_context or "").strip()
    if not answer or not context:
        return None
    answer_lower = answer.lower()
    calculation_cues = (
        "calculated",
        "calculate",
        "ratio",
        "share",
        "margin",
        "growth",
        "trend",
        "increase",
        "decrease",
        "declined",
        "improved",
        "percentage",
        "%",
        "times",
        "compared",
    )
    if not any(cue in answer_lower for cue in calculation_cues):
        return None

    answer_numbers = _numeric_slots(answer)
    context_numbers = _numeric_slots(context)
    shared_numbers = answer_numbers & context_numbers
    answer_years = set(_years_from_text(answer))
    context_years = set(_years_from_text(context))
    shared_years = answer_years & context_years
    if len(shared_numbers) < 2 and not (shared_numbers and shared_years):
        return None

    return {
        "schema_version": "evidence_derivation.v1",
        "support_type": "derived_supported",
        "operation": _calculation_kind(answer_lower),
        "computed_by": "grounded_synthesis_llm",
        "evidence_source": "graph_context_fallback",
        "operand_values": sorted(shared_numbers)[:8],
        "periods": sorted(shared_years)[:8],
        "missing_slots": [],
        "warnings": ["derivation_detected_from_answer_and_context; promote to deterministic_calculator when available"],
    }


def _calculation_kind(answer_lower: str) -> str:
    if any(token in answer_lower for token in ("ratio", "share", "times")):
        return "ratio"
    if any(token in answer_lower for token in ("margin", "percentage", "%")):
        return "percentage"
    if any(token in answer_lower for token in ("growth", "increase", "decrease", "delta")):
        return "delta"
    if "trend" in answer_lower:
        return "trend"
    return "derived_value"


def _numeric_slots(text: str) -> set[str]:
    slots: set[str] = set()
    for match in re.finditer(r"-?\$?\d[\d,]*\.?\d*(?:%| million| billion| thousand)?", str(text or ""), re.I):
        raw = match.group(0).strip().lower()
        normalized = raw.replace("$", "").replace(",", "").strip()
        normalized = normalized.removesuffix("%").strip()
        normalized = re.sub(r"\s+", " ", normalized)
        if normalized:
            slots.add(normalized)
    return slots


def _local_agent_pattern_receipt(
    *,
    configured_pattern: str,
    answer_source: str,
    reasoning_attempts: int,
    repair_budget: int,
    query_mode: str,
    support_assessment: Dict[str, Any],
) -> Dict[str, Any]:
    query_mode = normalize_query_mode(query_mode)
    support_status = str(support_assessment.get("status", "") or "").strip()
    if configured_pattern:
        pattern = configured_pattern
        reason = "agent_design_spec"
    elif query_mode == "graph_cot":
        pattern = "graph_cot"
        reason = "query_mode_requested"
    elif reasoning_attempts > 0 or support_status in {"partial", "unsupported"}:
        pattern = "reflection_chain"
        reason = "repair_or_partial_support"
    else:
        pattern = "semantic_direct"
        reason = "deterministic_supported_answer"
    return {
        "schema_version": "agent_pattern_receipt.v1",
        "pattern": pattern,
        "reason": reason,
        "answer_source": answer_source,
        "turn_count": 1 + max(0, reasoning_attempts),
        "tool_like_steps": 2 + max(0, reasoning_attempts),
        "repair_budget": max(0, int(repair_budget or 0)),
        "support_status": support_status,
        "query_mode": query_mode,
    }


def _estimate_local_token_usage(
    *,
    question: str,
    answer_text: str,
    cypher: str,
    records: Sequence[Dict[str, Any]],
    answer_source: str,
) -> Dict[str, Any]:
    if answer_source == "deterministic":
        return {
            "source": "not_applicable",
            "exact": True,
            "input_tokens_est": 0,
            "output_tokens_est": 0,
            "total_tokens_est": 0,
        }
    input_chars = len(question) + len(cypher) + len(str(list(records or [])))
    output_chars = len(answer_text or "")
    input_tokens = max(1, round(input_chars / 4)) if input_chars else 0
    output_tokens = max(1, round(output_chars / 4)) if output_chars else 0
    return {
        "source": "estimated_char_count",
        "exact": False,
        "input_tokens_est": input_tokens,
        "output_tokens_est": output_tokens,
        "total_tokens_est": input_tokens + output_tokens,
    }


def _query_diagnostics(
    *,
    records: Sequence[Dict[str, Any]],
    vector_context: str,
    error: str,
) -> List[Dict[str, str]]:
    if error:
        return [
            {
                "diagnosis_code": "query_execution_failed_or_contract_error",
                "message": error,
            }
        ]
    if not records and not vector_context:
        return [
            {
                "diagnosis_code": "query_no_graph_records",
                "message": "The graph query returned no records.",
            }
        ]
    return []


__all__ = ["build_local_query_metadata"]
