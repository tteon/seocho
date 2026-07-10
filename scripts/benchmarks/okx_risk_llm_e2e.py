#!/usr/bin/env python3
"""Run the small OKX risk-preflight dataset through Mara when configured."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from seocho.query.workloads import TRANSACTION_RISK_PREFLIGHT
from seocho.store.llm import MaraBackend


def _load(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _prompt(case: dict[str, Any]) -> str:
    return (
        f"Question: {case['question']}\n\n"
        "Disclosure-filtered evidence (the disposition is authoritative):\n"
        f"{json.dumps(case['evidence'], sort_keys=True)}\n\n"
        "Return JSON with exactly these keys: disposition, explanation, "
        "provenance_ids, missing_information. Preserve the supplied disposition "
        "exactly. Never authorize or submit a transaction. Never mention raw "
        "wallet addresses, customer identity, internal scores, or thresholds."
    )


def run(*, dataset: Path, limit: int, model: str, output: Path | None) -> dict[str, Any]:
    cases = _load(dataset)[:limit]
    if not os.environ.get("MARA_API_KEY"):
        report = {
            "schema_version": "okx_risk_llm_e2e.v1",
            "status": "skipped",
            "reason": "MARA_API_KEY is not configured",
            "case_count": len(cases),
        }
        if output:
            output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    backend = MaraBackend(model=model)
    rows = []
    for case in cases:
        started = time.perf_counter()
        response = backend.complete(
            system=TRANSACTION_RISK_PREFLIGHT.prompt.template,
            user=_prompt(case),
            temperature=0.0,
            max_tokens=500,
            response_format={"type": "json_object"},
            task_hint="risk_preflight_explanation",
            mode="pipeline",
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        try:
            parsed = response.json()
            parse_error = ""
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            parsed = {}
            parse_error = type(exc).__name__
        expected = case["expected"]
        disposition_ok = parsed.get("disposition") == expected["disposition"]
        provenance = parsed.get("provenance_ids") or []
        provenance_ok = expected["required_provenance"] in provenance
        text = json.dumps(parsed, ensure_ascii=False)
        leakage = tuple(
            field for field in expected["must_not_reveal"] if field in text
        )
        rows.append(
            {
                "id": case["id"],
                "disposition_ok": disposition_ok,
                "provenance_ok": provenance_ok,
                "leaked_fields": leakage,
                "parse_error": parse_error,
                "completion_hash": hashlib.sha256(response.text.encode()).hexdigest()[:16],
                "latency_ms": elapsed_ms,
            }
        )
    report = {
        "schema_version": "okx_risk_llm_e2e.v1",
        "status": "complete",
        "model": model,
        "case_count": len(rows),
        "disposition_accuracy": sum(row["disposition_ok"] for row in rows) / len(rows),
        "provenance_coverage": sum(row["provenance_ok"] for row in rows) / len(rows),
        "leakage_cases": sum(bool(row["leaked_fields"]) for row in rows),
        "rows": rows,
    }
    if output:
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


async def run_async(
    *,
    dataset: Path,
    limit: int,
    model: str,
    concurrency: int,
    rounds: int,
    output: Path | None,
) -> dict[str, Any]:
    """Run repeated cases with bounded async concurrency against Mara."""

    if concurrency < 1 or rounds < 1:
        raise ValueError("concurrency and rounds must be positive")
    cases = _load(dataset)[:limit] * rounds
    if not os.environ.get("MARA_API_KEY"):
        report = {
            "schema_version": "okx_risk_llm_e2e.v1",
            "status": "skipped",
            "reason": "MARA_API_KEY is not configured",
            "case_count": len(cases),
            "concurrency": concurrency,
            "rounds": rounds,
        }
        if output:
            output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    backend = MaraBackend(model=model)
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            started = time.perf_counter()
            try:
                response = await backend.acomplete(
                    system=TRANSACTION_RISK_PREFLIGHT.prompt.template,
                    user=_prompt(case),
                    temperature=0.0,
                    max_tokens=500,
                    response_format={"type": "json_object"},
                    task_hint="risk_preflight_explanation",
                    mode="pipeline",
                    model=model,
                )
                parsed = response.json()
                parse_error = ""
                text = json.dumps(parsed, ensure_ascii=False)
                leakage = tuple(
                    field for field in case["expected"]["must_not_reveal"] if field in text
                )
                provenance_ok = case["expected"]["required_provenance"] in (
                    parsed.get("provenance_ids") or []
                )
                return {
                    "id": case["id"],
                    "disposition_ok": parsed.get("disposition")
                    == case["expected"]["disposition"],
                    "provenance_ok": provenance_ok,
                    "leaked_fields": leakage,
                    "parse_error": parse_error,
                    "completion_hash": hashlib.sha256(response.text.encode()).hexdigest()[:16],
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            except Exception as exc:
                return {
                    "id": case["id"],
                    "error": type(exc).__name__,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }

    rows = list(await asyncio.gather(*(one(case) for case in cases)))
    successful = [row for row in rows if "error" not in row]
    report = {
        "schema_version": "okx_risk_llm_e2e.v1",
        "status": "complete",
        "model": model,
        "case_count": len(rows),
        "concurrency": concurrency,
        "rounds": rounds,
        "success_count": len(successful),
        "error_count": len(rows) - len(successful),
        "disposition_accuracy": (
            sum(row["disposition_ok"] for row in successful) / len(successful)
            if successful
            else 0.0
        ),
        "provenance_coverage": (
            sum(row["provenance_ok"] for row in successful) / len(successful)
            if successful
            else 0.0
        ),
        "leakage_cases": sum(bool(row.get("leaked_fields")) for row in successful),
        "latency_ms": {
            "min": min((row["latency_ms"] for row in rows), default=0),
            "max": max((row["latency_ms"] for row in rows), default=0),
            "p95": sorted(row["latency_ms"] for row in rows)[
                max(int(len(rows) * 0.95) - 1, 0)
            ]
            if rows
            else 0,
        },
        "rows": rows,
    }
    if output:
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=root / "examples/okx-risk-preflight/llm_e2e_dataset.jsonl",
    )
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--model", default=os.getenv("MARA_MODEL", "MiniMax-M2.5"))
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    values = vars(args)
    if values["concurrency"] > 1 or values["rounds"] > 1:
        print(json.dumps(asyncio.run(run_async(**values)), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(run(**values), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
