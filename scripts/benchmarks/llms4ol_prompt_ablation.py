#!/usr/bin/env python3
"""Run a paired basic-vs-LLMs4OL prompt framing diagnostic.

Output is content-free: source/prompt/response digests and aggregate
diagnostics only, never source text or model candidates. This allows a licensed
local corpus to be used without exporting its content, but it is not a semantic
accuracy benchmark without independently curated gold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from seocho.eval.experiment_observability import (
    direct_runtime_receipt,
    experiment_run_trace,
)
from seocho.llm_structured import StructuredOutputError, structured_complete
from seocho.metrics import get_metrics
from seocho.ontology.learning_prompt import (
    ARMS,
    CANDIDATE_SCHEMA,
    candidate_summary,
    prompt_for_arm,
)
from seocho.store.llm import create_llm_backend
from seocho.tracing import (
    configure_tracing_from_env,
    disable_tracing,
    flush_tracing,
    start_span,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_arm(*, backend: Any, model: str, arm: str, source: str) -> dict[str, Any]:
    system, user = prompt_for_arm(arm, source)
    started = time.perf_counter()
    outcome = "ok"
    error_type: str | None = None
    summary: dict[str, Any] = {
        "candidate_counts": {},
        "candidate_total": 0,
        "evidence_coverage": 0.0,
    }
    response_digest: str | None = None
    with start_span(
        "ontology_learning.prompt_ablation",
        metadata={
            "ontology_learning.arm": arm,
            "ontology_learning.model": model,
            "ontology_learning.source_sha256": _sha256(source),
            "ontology_learning.prompt_sha256": _sha256(system + "\n" + user),
        },
        tags=["ontology-learning", "prompt-ablation", f"arm:{arm}"],
    ) as span:
        try:
            payload = structured_complete(
                backend,
                system=system,
                user=user,
                schema=CANDIDATE_SCHEMA,
                model=model,
                temperature=0.0,
                max_tokens=4096,
                task_hint="ontology_learning",
            )
            response_digest = _sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=False)
            )
            summary = candidate_summary(payload)
            span.set_metadata(
                {
                    "ontology_learning.candidate_total": summary["candidate_total"],
                    "ontology_learning.evidence_coverage": summary["evidence_coverage"],
                }
            )
        except (StructuredOutputError, ValueError) as exc:
            outcome = "parse_error"
            error_type = type(exc).__name__
            span.set_metadata({"error.type": error_type})
        except Exception as exc:
            outcome = "error"
            error_type = type(exc).__name__
            span.set_metadata({"error.type": error_type})
    duration_s = time.perf_counter() - started
    metrics = get_metrics()
    metrics.record(
        "seocho.ontology.learning.prompt_ablation.duration",
        duration_s,
        {"arm": arm, "outcome": outcome},
    )
    metrics.add(
        "seocho.ontology.learning.prompt_ablation.count",
        attributes={"arm": arm, "outcome": outcome},
    )
    if outcome == "ok":
        metrics.record(
            "seocho.ontology.learning.prompt_ablation.evidence_coverage",
            summary["evidence_coverage"],
            {"arm": arm},
        )
        metric_kinds = {
            "terms": "term",
            "taxonomy": "taxonomy",
            "relations": "relation",
            "axioms": "axiom",
        }
        for kind, count in summary["candidate_counts"].items():
            metrics.add(
                "seocho.ontology.learning.candidate.count",
                count,
                {"kind": metric_kinds[kind]},
            )
    return {
        "arm": arm,
        "outcome": outcome,
        "error_type": error_type,
        "duration_s": round(duration_s, 6),
        "source_sha256": _sha256(source),
        "prompt_sha256": _sha256(system + "\n" + user),
        "response_sha256": response_digest,
        **summary,
        "semantic_quality_status": "unavailable_without_gold_and_review",
        "promotion_status": "not_attempted_review_required",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Local raw-material file; never copied to output.",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Content-free JSONL records."
    )
    parser.add_argument("--model", default="MiniMax-M2.7")
    parser.add_argument("--provider", default="mara")
    parser.add_argument("--workspace-id", default="ontology-learning-ablation")
    parser.add_argument("--max-chars", type=int, default=6000)
    args = parser.parse_args()
    if args.max_chars < 1:
        raise SystemExit("--max-chars must be positive")
    source = args.input.read_text(encoding="utf-8")[: args.max_chars]
    if not source.strip():
        raise SystemExit("input has no readable text")
    tracing_enabled = configure_tracing_from_env()
    try:
        backend = create_llm_backend(provider=args.provider, model=args.model)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with experiment_run_trace(
            receipt=direct_runtime_receipt(),
            run_name="llms4ol_prompt_ablation",
            workspace_id=args.workspace_id,
        ) as manifest:
            rows = [
                _run_arm(backend=backend, model=args.model, arm=arm, source=source)
                for arm in ARMS
            ]
            manifest["outcome"] = (
                "ok" if all(row["outcome"] == "ok" for row in rows) else "degraded"
            )
        _write_run(args, source, manifest, rows, tracing_enabled)
    finally:
        if tracing_enabled:
            flush_tracing()
            disable_tracing()


def _write_run(
    args: argparse.Namespace,
    source: str,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    tracing_enabled: bool,
) -> None:
    run = {
        "schema_version": "seocho.llms4ol_prompt_ablation.v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "provider": args.provider,
        "input_path_sha256": _sha256(str(args.input)),
        "input_source_sha256": _sha256(source),
        "input_chars": len(source),
        "runtime": manifest,
        "tracing_enabled": tracing_enabled,
        "records": rows,
        "interpretation": "Prompt framing diagnostic only; no gold semantic metric or promotion claim.",
    }
    with args.output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run, sort_keys=True) + "\n")
    flush_tracing()
    print(
        json.dumps(
            {
                "output": str(args.output),
                "outcomes": [row["outcome"] for row in rows],
                "input_chars": len(source),
                "tracing_enabled": tracing_enabled,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
