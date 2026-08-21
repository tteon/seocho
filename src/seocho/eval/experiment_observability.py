"""Receipts and root tracing for reproducible SEOCHO experiment runs.

This module deliberately records identities in traces/reports, never metric
labels.  It makes the execution runtime explicit: a direct SEOCHO call is not
silently described as an OpenAI Agents SDK run.
"""

from __future__ import annotations

import importlib.metadata
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from ..metrics import get_metrics
from ..tracing import current_backend_names, start_span


def direct_runtime_receipt() -> dict[str, Any]:
    """Return the runtime identity for the deterministic SDK E2E path."""
    return {
        "schema_version": "seocho.experiment_runtime_receipt.v1",
        "execution_runtime": "seocho_direct",
        "agents_sdk_version": None,
        "max_turns": None,
        "toolset_digest": None,
    }


def agents_sdk_runtime_receipt(*, max_turns: int, toolset_digest: str) -> dict[str, Any]:
    """Return an explicit receipt for a real Agents SDK Runner experiment."""
    try:
        version = importlib.metadata.version("openai-agents")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("openai-agents must be installed for agents_sdk experiments") from exc
    return {
        "schema_version": "seocho.experiment_runtime_receipt.v1",
        "execution_runtime": "agents_sdk",
        "agents_sdk_version": version,
        "max_turns": max(1, int(max_turns)),
        "toolset_digest": str(toolset_digest),
    }


@contextmanager
def experiment_run_trace(
    *,
    receipt: Mapping[str, Any],
    run_name: str,
    workspace_id: str,
) -> Iterator[dict[str, Any]]:
    """Create the parent span and a report-safe run manifest fragment."""
    run_id = uuid.uuid4().hex
    started = time.perf_counter()
    runtime = str(receipt.get("execution_runtime", "unknown"))
    manifest = {
        "run_id": run_id,
        "runtime_receipt": dict(receipt),
        "trace_backends": current_backend_names(),
        "trace_content_capture": str(os.getenv("SEOCHO_TRACE_CAPTURE_CONTENT", "")).lower()
        in {"1", "true", "yes", "on"},
    }
    outcome = "ok"
    with start_span(
        "experiment.run",
        metadata={
            "experiment.run_id": run_id,
            "experiment.name": run_name,
            "workspace_id": workspace_id,
            **dict(receipt),
        },
        tags=["experiment", f"runtime:{runtime}"],
    ) as span:
        try:
            yield manifest
        except Exception:
            outcome = "error"
            raise
        finally:
            elapsed = time.perf_counter() - started
            span.set_metadata({"experiment.outcome": outcome, "duration_ms": round(elapsed * 1000, 2)})
            metrics = get_metrics()
            metrics.record("seocho.experiment.run.duration", elapsed, {"runtime": runtime, "outcome": outcome})
            metrics.add("seocho.experiment.run.count", attributes={"runtime": runtime, "outcome": outcome})
            metrics.add("seocho.observability.trace_complete.count", attributes={"workflow": "experiment", "outcome": outcome})

