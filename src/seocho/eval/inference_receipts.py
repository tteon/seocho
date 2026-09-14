"""Read-only adapter for existing agentic experiment receipts and HTTP artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from .inference_finops import fingerprint


def import_receipts(
    root: Path, *, tenant: str, configuration_id: str, workload_id: str
) -> list[dict]:
    """Normalize existing per-case llm receipts without importing raw finance text.

    Successful HTTP calls remain distinct from case completion and correctness.
    Costs, streaming timings and measurements absent from source stay unknown.
    Caller must select a single run/configuration root, not pool hardware arms.
    """
    rows = []
    for path in sorted(root.glob("*/receipt.json")):
        receipt = json.loads(path.read_text())
        run_id = path.parent.name
        for call in receipt.get("llm", []):
            number = int(call["call"])
            response_path = path.parent / "http" / f"{number:02d}-response.json"
            request_path = path.parent / "http" / f"{number:02d}-request.json"
            response = (
                json.loads(response_path.read_text()) if response_path.exists() else {}
            )
            body = response.get("body") or {}
            usage = body.get("usage") or call.get("usage") or {}
            request = (
                json.loads(request_path.read_text()) if request_path.exists() else None
            )
            row = {
                "request_id": fingerprint(
                    [configuration_id, workload_id, run_id, number]
                ),
                "model": body.get("model")
                or call.get("response_model")
                or "unreported",
                "tenant": tenant,
                "workspace_id": receipt["workspace_id"],
                "route": receipt["arm"],
                "configuration_id": configuration_id,
                "workload_id": workload_id,
                "case_run_id": run_id,
                "case_status": receipt["status"],
                "final_answer_present": bool(str(receipt.get("answer") or "").strip()),
                "quality_score": receipt.get("quality_score"),
                "status": call.get("status", "unknown"),
                "duration_ms": call.get("wall_ms"),
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "cost_usd": None,
                "cost_basis": None,
                "ttft_ms": None,
                "itl_ms": None,
                "provider_response_id": body.get("id"),
                "trace_id": receipt.get("trace", {}).get("trace_id"),
                "input_digest": fingerprint(request.get("payload"))
                if request and request.get("payload")
                else None,
                "output_digest": fingerprint(body.get("choices"))
                if body.get("choices")
                else None,
                "cached_input_tokens": (usage.get("prompt_tokens_details") or {}).get(
                    "cached_tokens"
                ),
                "source_receipt": str(path.relative_to(root)),
                "call_number": number,
                "source": "agentic_receipt_and_http_artifact",
                "error_class": call.get("error_class"),
            }
            rows.append(row)
    if not rows:
        raise ValueError("No agentic case receipts containing llm calls found")
    return rows
