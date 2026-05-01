from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


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
) -> Dict[str, Any]:
    """Build the local SDK query observability contract.

    The shape intentionally mirrors runtime semantic responses so benchmark and
    trace consumers can compare SDK and runtime behavior without path-specific
    adapters.
    """

    normalized_breakdown = _roll_up_latency(latency_breakdown_ms)
    support_assessment = _local_support_assessment(
        records=records,
        answer_text=answer_text,
        vector_context=vector_context,
        error=error,
    )
    evidence_bundle = _local_evidence_bundle(
        database=database,
        intent_data=intent_data or {},
        records=records,
        support_assessment=support_assessment,
    )
    agent_pattern = _local_agent_pattern_receipt(
        configured_pattern=agent_design_pattern,
        answer_source=answer_source,
        reasoning_attempts=len(attempts),
        repair_budget=repair_budget,
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
        "support_assessment": support_assessment,
        "evidence_bundle": evidence_bundle,
        "query_diagnostics": diagnostics,
        "latency_breakdown_ms": normalized_breakdown,
        "token_usage": token_usage,
        "agent_pattern": agent_pattern,
    }

    return {
        "schema_version": "query_run_metadata.v1",
        "workspace_id": workspace_id,
        "database": database,
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
        "query_diagnostics": diagnostics,
        "token_usage": token_usage,
        "agent_pattern": agent_pattern,
        "answer_envelope": answer_envelope,
    }


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
        }
    if vector_context:
        return {
            "status": "partial",
            "reason": "vector_context_only",
            "row_count": 0,
            "missing_slots": ["graph_records"],
        }
    return {
        "status": "unsupported",
        "reason": "no_graph_records",
        "row_count": 0,
        "missing_slots": ["evidence"],
    }


def _local_evidence_bundle(
    *,
    database: str,
    intent_data: Dict[str, Any],
    records: Sequence[Dict[str, Any]],
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
    if supporting_fact:
        slot_fills["supporting_fact"] = supporting_fact

    grounded_slots = [slot for slot in focus_slots if slot in slot_fills]
    missing_slots = [slot for slot in focus_slots if slot not in slot_fills]
    coverage = round(len(grounded_slots) / max(1, len(focus_slots)), 4) if focus_slots else 1.0
    provenance = [
        {
            "database": database,
            "record_index": index,
            "keys": sorted(str(key) for key in record.keys()),
        }
        for index, record in enumerate(records[:3])
    ]

    return {
        "schema_version": "evidence_bundle.v2",
        "intent_id": intent,
        "focus_slots": focus_slots,
        "slot_fills": slot_fills,
        "grounded_slots": grounded_slots,
        "missing_slots": missing_slots,
        "provenance": provenance,
        "confidence": 1.0 if records else 0.0,
        "coverage": coverage,
        "support_assessment": dict(support_assessment),
    }


def _focus_slots_for_local_intent(intent: str) -> List[str]:
    if intent in {"financial_metric_lookup", "financial_metric_delta"}:
        return ["target_entity", "financial_metric", "period", "supporting_fact"]
    if intent == "relationship_lookup":
        return ["source_entity", "target_entity", "relation_paths", "supporting_fact"]
    return ["target_entity", "supporting_fact"]


def _first_supporting_fact(records: Sequence[Dict[str, Any]]) -> str:
    for record in records:
        for key in ("supporting_fact", "content", "text"):
            value = record.get(key) if isinstance(record, dict) else None
            if value:
                return str(value).strip()
    return ""


def _local_agent_pattern_receipt(
    *,
    configured_pattern: str,
    answer_source: str,
    reasoning_attempts: int,
    repair_budget: int,
    support_assessment: Dict[str, Any],
) -> Dict[str, Any]:
    support_status = str(support_assessment.get("status", "") or "").strip()
    if configured_pattern:
        pattern = configured_pattern
        reason = "agent_design_spec"
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
