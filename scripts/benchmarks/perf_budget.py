#!/usr/bin/env python3
"""Perf/behavioral budget assertion harness (seocho-6q9.2, area-benchmark).

Reads SEOCHO trace spans (JSONL) and asserts latency budgets — e.g. "service
startup completes within 800ms" and "no span exceeds 2s on the key journeys".
On any breach it exits non-zero and names every offending span, so a benchmark
run or CI step can gate on it.

Read-safe (no side effects). Self-contained: prefers ``seocho.tracing.read_jsonl``
when present (the SDK contract from seocho-6q9.1), otherwise falls back to an
inline JSONL reader so the harness works against any benchmark tree, including
branches that predate 6q9.1.

Usage::

    # Use a budget file:
    python scripts/benchmarks/perf_budget.py \
        --path traces/seocho.jsonl \
        --config scripts/benchmarks/perf_budgets.json

    # Or declare budgets inline:
    python scripts/benchmarks/perf_budget.py --startup-ms 800 --max-span-ms 2000

The JSONL is the canonical neutral artifact written by
``SEOCHO_TRACE_BACKEND=jsonl`` (default ``traces/seocho.jsonl``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Span loading (prefer the SDK reader, fall back to an inline one)
# ---------------------------------------------------------------------------

def _latency_ms(record: Dict[str, Any]) -> Optional[float]:
    """Best-effort latency (ms) for one span; mirrors seocho.tracing.span_latency_ms."""
    existing = record.get("latency_ms")
    if existing is not None:
        try:
            return float(existing)
        except (TypeError, ValueError):
            pass
    meta = record.get("metadata") or {}
    secs = meta.get("elapsed_seconds")
    if secs is not None:
        try:
            return float(secs) * 1000.0
        except (TypeError, ValueError):
            pass
    for key in ("elapsed_ms", "total_ms", "latency_ms"):
        val = meta.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


def _inline_read_jsonl(path: Path) -> List[Dict[str, Any]]:
    spans: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record["latency_ms"] = _latency_ms(record)
            spans.append(record)
    return spans


def load_spans(path: os.PathLike[str] | str) -> List[Dict[str, Any]]:
    """Load all spans from a JSONL trace file.

    Prefers the canonical SDK reader (seocho.tracing.read_jsonl) so the harness
    stays aligned with the SDK contract; falls back to an equivalent inline
    reader when the SDK lacks it (pre-6q9.1 trees).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"trace file not found: {p}")
    try:
        from seocho.tracing import read_jsonl  # type: ignore

        spans = read_jsonl(p)
        # Ensure latency_ms exists even if a future reader stops attaching it.
        for record in spans:
            if record.get("latency_ms") is None:
                record["latency_ms"] = _latency_ms(record)
        return spans
    except ImportError:
        return _inline_read_jsonl(p)


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

@dataclass
class Budget:
    """Latency budgets to assert against trace spans.

    startup_ms:
        Ceiling for startup/boot spans (matched by ``startup_names``).
    default_ceiling_ms:
        Ceiling applied to every other span with a measured latency.
    name_ceilings:
        Per-span-name overrides of ``default_ceiling_ms``.
    startup_names:
        Span names treated as service startup.
    ignore_names:
        Span names exempt from the default ceiling (e.g. known long-running jobs).
    """

    startup_ms: Optional[float] = None
    default_ceiling_ms: Optional[float] = None
    name_ceilings: Dict[str, float] = field(default_factory=dict)
    startup_names: List[str] = field(default_factory=list)
    ignore_names: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Budget":
        return cls(
            startup_ms=data.get("startup_ms"),
            default_ceiling_ms=data.get("default_ceiling_ms"),
            name_ceilings={str(k): float(v) for k, v in (data.get("name_ceilings") or {}).items()},
            startup_names=list(data.get("startup_names") or []),
            ignore_names=list(data.get("ignore_names") or []),
        )

    @classmethod
    def from_file(cls, path: os.PathLike[str] | str) -> "Budget":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class Violation:
    kind: str          # "startup" | "span"
    name: str
    observed_ms: float
    budget_ms: float
    timestamp: str = ""

    def __str__(self) -> str:
        ts = f" @ {self.timestamp}" if self.timestamp else ""
        return (
            f"[{self.kind}] {self.name}: {self.observed_ms:.0f}ms "
            f"> {self.budget_ms:.0f}ms budget{ts}"
        )


def evaluate(spans: List[Dict[str, Any]], budget: Budget) -> List[Violation]:
    """Return every span that breaches its budget. Empty list == all within budget."""
    violations: List[Violation] = []
    startup_names = set(budget.startup_names)
    ignore_names = set(budget.ignore_names)

    for record in spans:
        latency = _latency_ms(record)
        if latency is None:
            continue
        name = str(record.get("name", ""))
        ts = str(record.get("timestamp", ""))

        if name in startup_names:
            if budget.startup_ms is not None and latency > budget.startup_ms:
                violations.append(Violation("startup", name, latency, budget.startup_ms, ts))
            continue

        if name in ignore_names:
            continue

        ceiling = budget.name_ceilings.get(name, budget.default_ceiling_ms)
        if ceiling is not None and latency > ceiling:
            violations.append(Violation("span", name, latency, ceiling, ts))

    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_budget(args: argparse.Namespace) -> Budget:
    budget = Budget.from_file(args.config) if args.config else Budget()
    if args.startup_ms is not None:
        budget.startup_ms = args.startup_ms
    if args.max_span_ms is not None:
        budget.default_ceiling_ms = args.max_span_ms
    if not budget.startup_names:
        budget.startup_names = ["sdk.session.start", "startup", "boot"]
    return budget


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Assert SEOCHO trace latency budgets.")
    parser.add_argument(
        "--path",
        default=os.getenv("SEOCHO_TRACE_JSONL_PATH") or "traces/seocho.jsonl",
        help="JSONL trace file (default: $SEOCHO_TRACE_JSONL_PATH or traces/seocho.jsonl)",
    )
    parser.add_argument("--config", default=None, help="Budget JSON file")
    parser.add_argument("--startup-ms", type=float, default=None, help="Startup ceiling (ms)")
    parser.add_argument("--max-span-ms", type=float, default=None, help="Default per-span ceiling (ms)")
    parser.add_argument("--json", action="store_true", help="Emit violations as JSON")
    args = parser.parse_args(argv)

    try:
        spans = load_spans(args.path)
    except FileNotFoundError as exc:
        print(
            f"{exc}. Enable JSONL tracing with SEOCHO_TRACE_BACKEND=jsonl "
            f"(or pass --path).",
            file=sys.stderr,
        )
        return 2

    budget = _build_budget(args)
    if budget.startup_ms is None and budget.default_ceiling_ms is None and not budget.name_ceilings:
        print(
            "No budgets declared. Pass --config, --startup-ms, or --max-span-ms.",
            file=sys.stderr,
        )
        return 2

    violations = evaluate(spans, budget)

    if args.json:
        print(json.dumps([v.__dict__ for v in violations], indent=2))
    else:
        checked = sum(1 for s in spans if _latency_ms(s) is not None)
        if not violations:
            print(f"OK — {checked} timed span(s) within budget.")
        else:
            print(f"BUDGET BREACH — {len(violations)} of {checked} timed span(s) over budget:")
            for v in violations:
                print(f"  {v}")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
