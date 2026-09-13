"""Traces command: parser and local execution owner."""

from __future__ import annotations
import argparse
import json
import sys
from typing import Any


def register(subparsers: Any) -> None:
    traces_parser = subparsers.add_parser(
        "traces",
        help="Query trace spans from a JSONL file (read-safe, no server)",
    )
    traces_parser.add_argument(
        "--path",
        default=None,
        help="JSONL trace file (default: $SEOCHO_TRACE_JSONL_PATH or ./traces/seocho.jsonl)",
    )
    traces_parser.add_argument(
        "--min-latency-ms",
        type=float,
        default=None,
        help="Keep only spans at/above this latency (ms)",
    )
    traces_parser.add_argument("--name", default=None, help="Exact span-name match")
    traces_parser.add_argument(
        "--name-contains", default=None, help="Substring match on span name"
    )
    traces_parser.add_argument(
        "--tag",
        action="append",
        dest="tags",
        default=None,
        help="Require this tag (repeatable)",
    )
    traces_parser.add_argument(
        "--since", default=None, help="ISO timestamp lower bound (UTC)"
    )
    traces_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max spans to print (0 = all)",
    )
    traces_parser.add_argument(
        "--sort-latency",
        action="store_true",
        help="Sort by latency descending",
    )
    traces_parser.add_argument(
        "--output-json", "--json", action="store_true", help="Emit JSON"
    )


def handle(args: argparse.Namespace) -> int:
    """Query trace spans from a JSONL file (read side of the observe loop)."""
    from ..tracing import default_jsonl_path, read_jsonl

    path = args.path or default_jsonl_path()
    try:
        spans = read_jsonl(
            path,
            min_latency_ms=args.min_latency_ms,
            name=args.name,
            name_contains=args.name_contains,
            tags=args.tags,
            since=args.since,
        )
    except FileNotFoundError:
        print(
            f"No trace file at {path}. Enable JSONL tracing with "
            f"SEOCHO_TRACE_BACKEND=jsonl (or pass --path).",
            file=sys.stderr,
        )
        return 1

    if args.sort_latency:
        spans.sort(
            key=lambda r: (
                r.get("latency_ms") if r.get("latency_ms") is not None else -1.0
            ),
            reverse=True,
        )
    if args.limit and args.limit > 0:
        spans = spans[: args.limit]

    if getattr(args, "output_json", False):
        print(json.dumps(spans, indent=2, default=str))
        return 0

    if not spans:
        print("No matching spans.")
        return 0

    for record in spans:
        latency = record.get("latency_ms")
        latency_str = f"{latency:.0f}ms" if latency is not None else "-"
        tags = ",".join(record.get("tags") or [])
        print(
            f"{record.get('timestamp', '')}  {latency_str:>8}  {record.get('name', '')}  [{tags}]"
        )
    print(f"\n{len(spans)} span(s).")
    return 0
