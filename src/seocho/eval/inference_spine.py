"""Join portable engine spans and explicit cost ledgers without time-based guesses."""

from __future__ import annotations

from typing import Any, Iterable

from .inference_finops import finite, validate_requests


def join_engine_spans(requests: list[dict], spans: Iterable[dict]) -> list[dict]:
    """Consume normalized JSONL spans from the operator's OTLP file receiver.

    vLLM llm_request latency attributes use seconds. Multiple engine requests in
    one agent trace cannot be assigned by trace ID alone. Match response IDs,
    then fall back only to a unique llm_request in that trace. No sums of nested
    spans, and no decode-duration/output-token approximation labelled as ITL.
    """
    engine = [
        s
        for s in spans
        if s.get("name") == "llm_request" and s.get("service_name") == "vllm"
    ]
    result = validate_requests(requests)
    for row in result:
        ids = {
            row.get("response_request_id"),
            row.get("provider_response_id"),
            row["request_id"],
        } - {None, ""}
        matches = [
            s for s in engine if s.get("attributes", {}).get("gen_ai.request.id") in ids
        ]
        if (
            not matches
            and row.get("trace_id")
            and not row.get("provider_response_id")
            and not row.get("response_request_id")
            and sum(r.get("trace_id") == row["trace_id"] for r in result) == 1
        ):
            matches = [s for s in engine if s.get("trace_id") == row["trace_id"]]
        unique = {(s.get("trace_id"), s.get("span_id")): s for s in matches}
        row["engine_span_match"] = (
            "missing" if not unique else "ambiguous" if len(unique) != 1 else "exact"
        )
        if len(unique) != 1:
            continue
        span = next(iter(unique.values()))
        row["engine_trace_id"] = span.get("trace_id")
        row["engine_span_id"] = span.get("span_id")
        attrs = span.get("attributes", {})
        for field, attr in {
            "engine_ttft_ms": "gen_ai.latency.time_to_first_token",
            "queue_ms": "gen_ai.latency.time_in_queue",
            "prefill_ms": "gen_ai.latency.time_in_model_prefill",
            "decode_ms": "gen_ai.latency.time_in_model_decode",
        }.items():
            seconds = finite(attrs.get(attr))
            if seconds is not None:
                row[field] = seconds * 1000
                row[field + "_source"] = "vllm_llm_request_span"
        row["engine_status"] = span.get("status")
    return result


def apply_cost_ledger(requests: list[dict], ledger: list[dict]) -> list[dict]:
    """Attach operator billing/allocation entries. No utilization-based charging."""
    rows = validate_requests(requests)
    lookup = {r["request_id"]: r for r in rows}
    seen: set[str] = set()
    for entry in ledger:
        key = entry.get("request_id")
        if key not in lookup or key in seen:
            raise ValueError("Cost ledger has unknown or duplicate request ID")
        seen.add(key)
        if lookup[key].get("cost_usd") is not None:
            raise ValueError("Request already priced; preserve original accounting")
        if finite(entry.get("cost_usd")) is None or not entry.get("cost_basis"):
            raise ValueError(
                "Cost entry requires nonnegative USD and an explicit basis"
            )
        lookup[key].update(
            cost_usd=float(entry["cost_usd"]), cost_basis=entry["cost_basis"]
        )
    return rows


def allocate_run_cost(
    requests: list[dict], *, total_usd: float, unallocated_usd: float, basis: str
) -> tuple[list[dict], dict[str, Any]]:
    """Explicit estimated allocation, conserving total resource cost.

    Allocate the attributed portion by reported input + output tokens. This is
    a FinOps allocation rule, not measured GPU consumption. Unknown usage makes
    this rule inapplicable; rejected requests/retries remain in the ledger.
    """
    rows = validate_requests(requests)
    if (
        finite(total_usd) is None
        or finite(unallocated_usd) is None
        or unallocated_usd > total_usd
        or not basis
    ):
        raise ValueError("Valid total, unallocated USD and source basis required")
    if not rows or any(
        r.get("cost_usd") is not None
        or r.get("input_tokens") is None
        or r.get("output_tokens") is None
        for r in rows
    ):
        raise ValueError(
            "Token allocation requires unpriced requests with complete usage"
        )
    weights = [r["input_tokens"] + r["output_tokens"] for r in rows]
    total_tokens = sum(weights)
    if total_tokens == 0:
        raise ValueError("No reported tokens for allocation")
    attributed = total_usd - unallocated_usd
    for row, weight in zip(rows, weights):
        row.update(
            cost_usd=attributed * weight / total_tokens,
            cost_basis="estimated_token_share: " + basis,
        )
    return rows, {
        "total_resource_cost_usd": total_usd,
        "unallocated_cost_usd": unallocated_usd,
        "attributed_cost_usd": attributed,
        "basis": basis,
        "method": "estimated_token_share_not_gpu_measurement",
    }
