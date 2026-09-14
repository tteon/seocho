"""Source-aware request accounting and engine telemetry for operator reports.

Tenant/route identifiers belong in this private ledger, not production metric labels.
Engine windows cannot establish per-tenant GPU use or token-level client latency.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def parse_prometheus(text: str) -> list[dict[str, Any]]:
    """Read numeric Prometheus samples; preserve series labels and missing data."""
    result = []
    for line in text.splitlines():
        match = re.match(r"^([\w:]+)(?:\{(.*)\})?\s+([^\s]+)(?:\s+.*)?$", line)
        if not match:
            continue
        value = finite(match[3])
        if value is None:
            continue
        labels = {
            k: json.loads('"' + v + '"')
            for k, v in re.findall(r'(\w+)="((?:\\.|[^"\\])*)"', match[2] or "")
        }
        result.append({"name": match[1], "labels": labels, "value": value})
    return result


def counter_delta(before: list[dict], after: list[dict], name: str) -> float | None:
    """Reject reset or changed series sets instead of inventing a positive delta."""
    for candidate in (name, name + "_total"):

        def series(samples: list[dict]) -> dict[str, float]:
            return {
                fingerprint(r["labels"]): r["value"]
                for r in samples
                if r["name"] == candidate
            }

        left, right = series(before), series(after)
        if not left or set(left) != set(right):
            continue
        if any(right[k] < v for k, v in left.items()):
            return None
        return sum(right[k] - v for k, v in left.items())
    return None


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    return (
        numerator / denominator
        if numerator is not None and denominator is not None and denominator > 0
        else None
    )


def engine_window(before: str, after: str, dcgm: str = "") -> dict[str, Any]:
    left, right = parse_prometheus(before), parse_prometheus(after)

    def delta(name: str) -> float | None:
        return counter_delta(left, right, "vllm:" + name)

    result: dict[str, Any] = {
        "scope": "engine_window_not_tenant",
        "source": "vllm_prometheus",
    }
    for short, metric in {
        "ttft_ms": "time_to_first_token_seconds",
        "itl_ms": "inter_token_latency_seconds",
        "queue_ms": "request_queue_time_seconds",
        "prefill_ms": "request_prefill_time_seconds",
        "decode_ms": "request_decode_time_seconds",
    }.items():
        mean = ratio(delta(metric + "_sum"), delta(metric + "_count"))
        result[short] = None if mean is None else mean * 1000
    for field, metric in {
        "input_tokens": "prompt_tokens",
        "output_tokens": "generation_tokens",
        "accepted_tokens": "spec_decode_num_accepted_tokens",
        "draft_tokens": "spec_decode_num_draft_tokens",
        "draft_steps": "spec_decode_num_drafts",
        "cache_hits": "prefix_cache_hits",
        "cache_queries": "prefix_cache_queries",
    }.items():
        result[field] = delta(metric)
    result["accepted_token_rate"] = ratio(
        result["accepted_tokens"], result["draft_tokens"]
    )
    if result["accepted_token_rate"] is not None and result["accepted_token_rate"] > 1:
        result["accepted_token_rate"] = None
    result["accepted_per_draft_step"] = ratio(
        result["accepted_tokens"], result["draft_steps"]
    )
    result["prefix_hit_rate"] = ratio(result["cache_hits"], result["cache_queries"])
    if result["prefix_hit_rate"] is not None and result["prefix_hit_rate"] > 1:
        result["prefix_hit_rate"] = None
    result["gpu"] = [
        r
        for r in parse_prometheus(dcgm)
        if r["name"]
        in {
            "DCGM_FI_DEV_GPU_UTIL",
            "DCGM_FI_DEV_FB_USED",
            "DCGM_FI_DEV_POWER_USAGE",
        }
        and r["value"] < 1e15
        and (r["name"] != "DCGM_FI_DEV_GPU_UTIL" or r["value"] <= 100)
    ]
    result["gpu_source"] = "dcgm_exporter" if result["gpu"] else None
    result["missing"] = [
        k
        for k in [
            "ttft_ms",
            "itl_ms",
            "queue_ms",
            "prefill_ms",
            "decode_ms",
            "accepted_token_rate",
            "gpu_source",
        ]
        if result[k] is None
    ]
    return result


def validate_requests(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result, seen = [], set()
    for raw in records:
        r = dict(raw)
        for field in (
            "request_id",
            "model",
            "tenant",
            "route",
            "workspace_id",
            "configuration_id",
            "workload_id",
        ):
            if not isinstance(r.get(field), str) or not r[field]:
                raise ValueError("Missing request identity: " + field)
        if r["request_id"] in seen:
            raise ValueError(
                "Duplicate request would double-charge: " + r["request_id"]
            )
        seen.add(r["request_id"])
        for field in (
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "duration_ms",
            "ttft_ms",
            "itl_ms",
            "queue_ms",
            "prefill_ms",
            "decode_ms",
        ):
            if r.get(field) is not None and finite(r[field]) is None:
                raise ValueError("Invalid nonnegative measurement: " + field)
            if r.get(field) is not None:
                r[field] = float(r[field])
        for field in ("input_tokens", "output_tokens"):
            if r.get(field) is not None:
                if not r[field].is_integer():
                    raise ValueError("Token counts must be integers")
                r[field] = int(r[field])
        if r.get("cost_usd") is not None and not r.get("cost_basis"):
            raise ValueError("A cost needs a billing/allocation basis")
        if r.get("itl_ms") is not None and r.get("itl_source") not in {
            "engine_trace",
            "token_timestamps",
        }:
            raise ValueError("SSE chunk timing is not token ITL")
        result.append(r)
    return result


def summarize(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    requests = validate_requests(records)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in requests:
        groups[
            tuple(
                r[k]
                for k in ("model", "tenant", "route", "configuration_id", "workload_id")
            )
        ].append(r)
    summary = []
    for key, rows in sorted(groups.items()):
        measured_cost = [r for r in rows if r.get("cost_usd") is not None]
        complete_cost = len(measured_cost) == len(rows)
        output_known = all(r.get("output_tokens") is not None for r in rows)
        input_known = all(r.get("input_tokens") is not None for r in rows)
        cost = sum(r["cost_usd"] for r in measured_cost) if complete_cost else None
        generated = (
            sum(r.get("output_tokens") or 0 for r in rows) if output_known else None
        )
        prompt = sum(r.get("input_tokens") or 0 for r in rows) if input_known else None
        item = dict(
            zip(("model", "tenant", "route", "configuration_id", "workload_id"), key)
        )
        item.update(
            requests=len(rows),
            errors=sum(r.get("status") != "ok" for r in rows),
            cost_usd=cost,
            known_cost_usd=sum(r["cost_usd"] for r in measured_cost),
            priced_requests=len(measured_cost),
            input_tokens=prompt,
            output_tokens=generated,
            cost_per_1m_output_tokens=ratio(
                None if cost is None else cost * 1e6, generated
            ),
            cost_per_1m_total_tokens=ratio(
                None if cost is None else cost * 1e6,
                prompt + generated
                if prompt is not None and generated is not None
                else None,
            ),
        )
        for metric in (
            "duration_ms",
            "ttft_ms",
            "itl_ms",
            "queue_ms",
            "prefill_ms",
            "decode_ms",
        ):
            values = [float(r[metric]) for r in rows if r.get(metric) is not None]
            item[metric + "_p50"] = percentile(values, 0.5)
            item[metric + "_p95"] = percentile(values, 0.95)
            item[metric + "_coverage"] = len(values)
        summary.append(item)
    return {
        "schema": "seocho.inference_finops.v1",
        "groups": summary,
        "requests": requests,
        "cost_boundary": "Recorded attributed request costs only; unknown/idle/unallocated resource costs must be supplied separately, never inferred from GPU utilization.",
    }


def alerts(
    current: dict,
    baseline: dict | None = None,
    *,
    min_requests: int = 20,
    cost_factor: float = 1.5,
    latency_factor: float = 1.5,
) -> list[dict]:
    """Compare matched workload/configuration cohorts; emit local operator alerts."""
    if min_requests < 1 or cost_factor <= 1 or latency_factor <= 1:
        raise ValueError("Positive sample floor and factors greater than one required")
    keys = ("model", "tenant", "route", "configuration_id", "workload_id")
    prior = {tuple(r[k] for k in keys): r for r in (baseline or {}).get("groups", [])}
    result = []
    for row in current["groups"]:
        identity = {k: row[k] for k in keys}
        if row["errors"]:
            result.append(
                {**identity, "kind": "request_errors", "count": row["errors"]}
            )
        old = prior.get(tuple(row[k] for k in keys))
        if not old or min(row["requests"], old["requests"]) < min_requests:
            continue
        for field, factor, kind in [
            ("cost_per_1m_output_tokens", cost_factor, "cost_spike"),
            ("ttft_ms_p95", latency_factor, "ttft_drift"),
        ]:
            if (
                field == "ttft_ms_p95"
                and min(row.get("ttft_ms_coverage", 0), old.get("ttft_ms_coverage", 0))
                < min_requests
            ):
                continue
            a, b = row.get(field), old.get(field)
            if a is not None and b is not None and b > 0 and a > b * factor:
                result.append(
                    {
                        **identity,
                        "kind": kind,
                        "current": a,
                        "baseline": b,
                        "factor": factor,
                    }
                )
    outputs: dict[tuple, set[str]] = defaultdict(set)
    reported_cases: set[str] = set()
    for r in current["requests"]:
        case = r.get("case_run_id")
        if (
            case
            and case not in reported_cases
            and (r.get("case_status") != "final" or not r.get("final_answer_present"))
        ):
            result.append(
                {
                    **{k: r[k] for k in keys},
                    "kind": "case_incomplete_or_empty_answer",
                    "case_run_id": case,
                    "case_status": r.get("case_status"),
                }
            )
            reported_cases.add(case)
        if r.get("status") == "ok" and r.get("input_digest") and r.get("output_digest"):
            outputs[tuple(r[k] for k in keys) + (r["input_digest"],)].add(
                r["output_digest"]
            )
    for key, values in outputs.items():
        if len(values) > 1:
            result.append(
                {
                    **dict(zip(keys, key)),
                    "kind": "output_variation_not_quality_verdict",
                    "input_digest": key[-1],
                    "distinct_outputs": len(values),
                }
            )
    return result
