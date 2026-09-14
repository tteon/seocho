"""Bounded HTTP probe and draft-target serving plans, without owning deployment."""

from __future__ import annotations

from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import time
from typing import Any
import uuid

import httpx

from .inference_finops import engine_window, fingerprint


def serving_plan(
    target: str, draft: str, revision: str, *, speculative_tokens: int = 5
) -> dict[str, Any]:
    if not target or not draft or not revision or speculative_tokens < 1:
        raise ValueError("Pinned target and draft snapshot paths/revision are required")
    arms = []
    for speculative, prefix, chunked in itertools.product((False, True), repeat=3):
        name = f"spec{int(speculative)}-prefix{int(prefix)}-chunked{int(chunked)}"
        argv = [
            "vllm",
            "serve",
            target,
            "--revision",
            revision,
            "--tokenizer-revision",
            revision,
            "--host",
            "127.0.0.1",
            "--enable-prefix-caching" if prefix else "--no-enable-prefix-caching",
            "--enable-chunked-prefill" if chunked else "--no-enable-chunked-prefill",
        ]
        if speculative:
            argv += [
                "--speculative-config",
                json.dumps(
                    {
                        "method": "draft_model",
                        "model": draft,
                        "num_speculative_tokens": speculative_tokens,
                    }
                ),
            ]
        arms.append(
            {
                "name": name,
                "argv": argv,
                "speculative": speculative,
                "prefix_caching": prefix,
                "chunked_prefill": chunked,
            }
        )
    return {
        "schema": "seocho.serving_factorial.v1",
        "target": target,
        "target_revision": revision,
        "draft": draft,
        "draft_requirement": "Use an immutable local snapshot path; validate tokenizer/architecture support with the installed vLLM version.",
        "arms": arms,
        "execution": "Operator-owned isolated server; do not restart a live quality baseline.",
        "comparison": "Identical prompts, sampling, hardware, concurrency and warm/cold policy. One repeated workload per arm; do not infer an effect from a single combined-on comparison.",
        "acceptance_definition": "accepted draft tokens / proposed draft tokens from matched engine-counter deltas; bonus target tokens excluded",
        "cost_boundary": "Include draft and target resources, retries, setup and unallocated idle cost.",
    }


def read_metric(client: httpx.Client, url: str | None) -> tuple[str, str | None]:
    if not url:
        return "", "not_configured"
    try:
        # Metric endpoints can be on a different host; never forward LLM keys.
        request = client.build_request("GET", url, timeout=10)
        request.headers.pop("Authorization", None)
        response = client.send(request)
        response.raise_for_status()
        return response.text, None
    except httpx.HTTPError as exc:
        return "", type(exc).__name__


def probe(
    *,
    base_url: str,
    model: str,
    prompts: list[dict],
    out: Path,
    tenant: str,
    workspace_id: str,
    route: str,
    configuration_id: str,
    max_calls: int,
    max_tokens: int = 256,
    metrics_url: str | None = None,
    dcgm_url: str | None = None,
    api_key: str = "local-vllm",
) -> dict[str, Any]:
    if not prompts or len(prompts) > max_calls or max_tokens < 1:
        raise ValueError("Probe exceeds the explicit request/output budget")
    if any(
        not isinstance(p.get("messages"), list) or not p["messages"] for p in prompts
    ):
        raise ValueError("Each prompt requires a nonempty messages list")
    if not all((model, tenant, workspace_id, route, configuration_id)):
        raise ValueError("Request identity fields must not be empty")
    out.mkdir(parents=True, exist_ok=False)
    os.chmod(out, 0o700)
    manifest = {
        "model": model,
        "tenant": tenant,
        "workspace_id": workspace_id,
        "route": route,
        "configuration_id": configuration_id,
        "workload_id": fingerprint(prompts),
        "requests": len(prompts),
        "max_tokens": max_tokens,
        "temperature": 0,
        "cache_state": "uncontrolled; identify cold/warm through external run manifest",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "engine_window_exclusive": False,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rows = []
    with httpx.Client(
        headers={"Authorization": "Bearer " + api_key}, timeout=120
    ) as client:
        before, before_error = read_metric(client, metrics_url)
        for prompt in prompts:
            request_id, trace_id = uuid.uuid4().hex, uuid.uuid4().hex
            payload = {
                "model": model,
                "messages": prompt["messages"],
                "temperature": 0,
                "max_tokens": max_tokens,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            started = time.perf_counter()
            start_ns, span_id = time.time_ns(), uuid.uuid4().hex[:16]
            row = {
                **manifest,
                "request_id": request_id,
                "trace_id": trace_id,
                "status": "error",
                "input_digest": fingerprint(payload),
                "input_tokens": None,
                "output_tokens": None,
                "cost_usd": None,
                "cost_basis": None,
                "ttft_ms": None,
                "itl_ms": None,
                "ttft_source": "client_first_output_chunk",
                "itl_source": None,
            }
            output, arrivals = [], []
            try:
                headers = {
                    "traceparent": f"00-{trace_id}-{span_id}-01",
                    "X-Request-Id": request_id,
                }
                with client.stream(
                    "POST",
                    base_url.rstrip("/") + "/chat/completions",
                    json=payload,
                    headers=headers,
                ) as response:
                    response.raise_for_status()
                    row["response_request_id"] = response.headers.get("x-request-id")
                    finished = False
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        content = line[5:].strip()
                        if content == "[DONE]":
                            finished = True
                            continue
                        event = json.loads(content)
                        if event.get("id"):
                            row["provider_response_id"] = event["id"]
                        usage = event.get("usage")
                        if usage:
                            row.update(
                                input_tokens=usage.get("prompt_tokens"),
                                output_tokens=usage.get("completion_tokens"),
                            )
                        for choice in event.get("choices", []):
                            delta = choice.get("delta") or {}
                            generated = {
                                k: delta[k]
                                for k in ("content", "reasoning_content", "tool_calls")
                                if delta.get(k)
                            }
                            if generated:
                                arrivals.append((time.perf_counter() - started) * 1000)
                                output.append(generated)
                            if choice.get("finish_reason") is not None:
                                row["finish_reason"] = choice["finish_reason"]
                    if not finished:
                        raise ValueError("SSE stream ended without DONE")
                row["status"] = "ok"
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                row["error_class"] = type(exc).__name__
            row.update(
                duration_ms=(time.perf_counter() - started) * 1000,
                ttft_ms=arrivals[0] if arrivals else None,
                inter_chunk_latency_ms=[b - a for a, b in zip(arrivals, arrivals[1:])],
                output_digest=fingerprint(output),
                output_chunk_count=len(arrivals),
            )
            rows.append(row)
            with (out / "requests.jsonl").open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            span = {
                "schema_version": "seocho.inference_client_span.v1",
                "service_name": "seocho-inference-probe",
                "name": "inference.request",
                "kind": "CLIENT",
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": None,
                "start_unix_ns": start_ns,
                "end_unix_ns": time.time_ns(),
                "status": row["status"],
                "attributes": {
                    k: row.get(k)
                    for k in (
                        "request_id",
                        "workspace_id",
                        "tenant",
                        "model",
                        "route",
                        "configuration_id",
                        "input_digest",
                        "output_digest",
                        "error_class",
                        "provider_response_id",
                    )
                },
            }
            with (out / "client-spans.jsonl").open("a") as handle:
                handle.write(json.dumps(span) + "\n")
        after, after_error = read_metric(client, metrics_url)
        dcgm, dcgm_error = read_metric(client, dcgm_url)
    for name, content in [
        ("vllm-before.prom", before),
        ("vllm-after.prom", after),
        ("dcgm.prom", dcgm),
    ]:
        (out / name).write_text(content)
    telemetry = engine_window(before, after, dcgm)
    telemetry["collection_errors"] = {
        "before": before_error,
        "after": after_error,
        "dcgm": dcgm_error,
    }
    (out / "telemetry.json").write_text(json.dumps(telemetry, indent=2) + "\n")
    return {
        "requests": len(rows),
        "errors": sum(r["status"] != "ok" for r in rows),
        "telemetry": telemetry,
    }
