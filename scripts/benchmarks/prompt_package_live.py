#!/usr/bin/env python3
"""Measure cold/warm SEOCHO Prompt Package behavior on an OpenAI-compatible API.

The first request in each trial uses a unique workspace-stable prefix.  The
second keeps that prefix byte-identical and changes only the final question.
No cache hit is inferred from latency; provider usage is reported separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from seocho.prompt_ir import (
    PromptCacheScope,
    PromptSection,
    PromptSectionKind,
    PromptSource,
    PromptStability,
    PromptStage,
    StagePromptSpec,
)


def _stable_contract(trial: str) -> str:
    schema = "\n".join(
        f"- property_{i:03d}: transaction-memory field {i:03d}; preserve provenance and workspace scope"
        for i in range(180)
    )
    return f"""SEOCHO blockchain agent-memory contract, revision {trial}.
You answer only from supplied transaction evidence. Never authorize, sign, or
broadcast a transfer. Return compact JSON with keys answer, evidence_ids,
confidence. Agent, Wallet, Transaction, MemoryRevision, and PolicyVersion are
the only node types. PAID, RECEIVED, CAUSED_BY, SUPERSEDES, and GOVERNED_BY are
the only relationships. All traversal is workspace scoped.

Canonical schema glossary:
{schema}
"""


def _spec(trial: str, question: str) -> StagePromptSpec:
    evidence = (
        "tx-100: agent-a paid wallet-b 0.25 BTC at height 900001; source=bitcoin-mainnet.\n"
        "tx-101: wallet-b paid agent-c 0.10 BTC at height 900009; source=bitcoin-mainnet."
    )
    return StagePromptSpec(
        stage=PromptStage.ANSWER_SYNTHESIS,
        system_sections=[
            PromptSection(
                section_id="contract",
                kind=PromptSectionKind.CONTRACT,
                source=PromptSource.SYSTEM_CONTRACT,
                title="System Contract",
                content=_stable_contract(trial),
                stability=PromptStability.WORKSPACE,
                cache_scope=PromptCacheScope.WORKSPACE,
            )
        ],
        user_sections=[
            PromptSection(
                section_id="evidence",
                kind=PromptSectionKind.EVIDENCE,
                source=PromptSource.RETRIEVAL_EVIDENCE,
                title="Retrieved Evidence",
                content=evidence,
                cacheable=False,
                stability=PromptStability.REQUEST,
                cache_scope=PromptCacheScope.NONE,
            ),
            PromptSection(
                section_id="question",
                kind=PromptSectionKind.USER_INPUT,
                source=PromptSource.USER_INPUT,
                title="Question",
                content=question,
                cacheable=False,
                stability=PromptStability.REQUEST,
                cache_scope=PromptCacheScope.NONE,
            ),
        ],
    )


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump(exclude_none=True)
    return dict(usage) if isinstance(usage, dict) else {}


def _cached_tokens(usage: dict[str, Any]) -> int | None:
    if usage.get("cached_tokens") is not None:
        return int(usage["cached_tokens"])
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    value = details.get("cached_tokens")
    return int(value) if value is not None else None


def _call(
    client: OpenAI,
    model: str,
    request: dict[str, Any],
    *,
    temperature: float,
    max_completion_tokens: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    first_token: float | None = None
    pieces: list[str] = []
    usage: dict[str, Any] = {}
    extra_body = {key: value for key, value in request.items() if key != "messages"}
    stream = client.chat.completions.create(
        model=model,
        messages=request["messages"],
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
        stream=True,
        stream_options={"include_usage": True},
        extra_body=extra_body or None,
    )
    for chunk in stream:
        if getattr(chunk, "usage", None) is not None:
            usage = _usage_dict(chunk.usage)
        for choice in chunk.choices or []:
            content = getattr(choice.delta, "content", None)
            if content:
                if first_token is None:
                    first_token = time.perf_counter()
                pieces.append(content)
    ended = time.perf_counter()
    text = "".join(pieces)
    lower = text.lower()
    return {
        "ttft_s": round((first_token or ended) - started, 6),
        "latency_s": round(ended - started, 6),
        "usage": usage,
        "cached_tokens": _cached_tokens(usage),
        "response_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "response_chars": len(text),
        "quality": {
            "mentions_expected_transaction": "tx-100" in lower or "tx-101" in lower,
            "contains_evidence_key": "evidence_ids" in lower,
            "forbidden_authorization_language": "authorize the transfer" in lower,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--base-url", default="https://api.cloud.mara.com/v1")
    parser.add_argument("--provider", default="mara")
    parser.add_argument("--api-key-env", default="MARA_API_KEY")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-completion-tokens", type=int, default=500)
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    key = os.environ.get(args.api_key_env)
    if not key:
        raise SystemExit(f"{args.api_key_env} is required")
    client = OpenAI(api_key=key, base_url=args.base_url, timeout=120.0, max_retries=2)
    records: list[dict[str, Any]] = []
    for model in args.models:
        for index in range(args.trials):
            trial = f"{datetime.now(timezone.utc).date()}-{uuid.uuid4().hex}"
            for phase, question in (
                ("cold", "Which transaction proves that agent-a paid wallet-b?"),
                ("warm", "How much BTC did wallet-b later pay agent-c?"),
            ):
                package = _spec(trial, question).render_package(
                    backend=args.provider,
                    cache_key=f"seocho-agent-memory-{model}-{index}",
                )
                if args.disable_thinking:
                    package["request"]["thinking"] = {"type": "disabled"}
                result = _call(
                    client,
                    model,
                    package["request"],
                    temperature=args.temperature,
                    max_completion_tokens=args.max_completion_tokens,
                )
                records.append(
                    {
                        "model": model,
                        "trial": index,
                        "phase": phase,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "prompt_receipt": package["receipt"],
                        **result,
                    }
                )
    summaries: dict[str, Any] = {}
    for model in args.models:
        rows = [row for row in records if row["model"] == model]
        cold = [row for row in rows if row["phase"] == "cold"]
        warm = [row for row in rows if row["phase"] == "warm"]
        provider_cold_ttft = [
            float(row["usage"]["time_to_first_token"])
            for row in cold
            if row["usage"].get("time_to_first_token") is not None
        ]
        provider_warm_ttft = [
            float(row["usage"]["time_to_first_token"])
            for row in warm
            if row["usage"].get("time_to_first_token") is not None
        ]
        reports_cache = any(row["cached_tokens"] is not None for row in rows)
        summaries[model] = {
            "requests": len(rows),
            "cold_ttft_median_s": statistics.median(row["ttft_s"] for row in cold),
            "warm_ttft_median_s": statistics.median(row["ttft_s"] for row in warm),
            "provider_cold_ttft_median_s": (
                statistics.median(provider_cold_ttft) if provider_cold_ttft else None
            ),
            "provider_warm_ttft_median_s": (
                statistics.median(provider_warm_ttft) if provider_warm_ttft else None
            ),
            "cold_latency_median_s": statistics.median(row["latency_s"] for row in cold),
            "warm_latency_median_s": statistics.median(row["latency_s"] for row in warm),
            "provider_reports_cached_tokens": reports_cache,
            "cache_evidence": "provider_reported" if reports_cache else "unverified",
            "cached_tokens": [row["cached_tokens"] for row in rows],
            "quality_pass_rate": sum(
                row["quality"]["mentions_expected_transaction"]
                and row["quality"]["contains_evidence_key"]
                and not row["quality"]["forbidden_authorization_language"]
                for row in rows
            ) / len(rows),
        }
    output = {
        "schema_version": "seocho.prompt-cache-live.v1",
        "provider": args.provider,
        "base_url": args.base_url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "unique-prefix cold request followed by byte-identical-prefix warm request",
        "cache_hit_inference": "provider usage only; latency is reported but never treated as proof",
        "summaries": summaries,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summaries, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
