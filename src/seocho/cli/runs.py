"""Saved experiment evidence commands. All operations are offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..run_comparison import CHANGEABLE, compare_runs, load_report, render_comparison


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "runs", help="Inspect and compare saved E2E evidence without model calls"
    )
    commands = parser.add_subparsers(dest="runs_command", required=True)
    compare = commands.add_parser(
        "compare",
        help="Compare baseline and candidate report.json files or run directories",
    )
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--change", action="append", choices=CHANGEABLE, default=[])
    compare.add_argument(
        "--hypothesis", default="", help="What changed and what improvement you expect"
    )
    compare.add_argument(
        "--output-dir", type=Path, help="Write comparison.json/md to a new directory"
    )
    compare.add_argument("--json", action="store_true", dest="output_json", help="Emit JSON")

    view = commands.add_parser(
        "view", help="Export a local interactive HTML view of saved results"
    )
    view.add_argument("report", type=Path)
    view.add_argument("--baseline", type=Path)
    view.add_argument("--change", action="append", choices=CHANGEABLE, default=[])
    view.add_argument("--hypothesis", default="")
    view.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New HTML file; existing files are refused",
    )

    dashboard = commands.add_parser(
        "dashboard", help="Browse saved experiments in a local read-only dashboard"
    )
    dashboard.add_argument(
        "directories",
        type=Path,
        nargs="*",
        help="Directories containing report.json files (default: ./runs)",
    )
    dashboard.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Loopback HTTP port (0 selects an available port)",
    )


def handle(args: argparse.Namespace) -> int:
    if args.runs_command == "dashboard":
        from ..dashboard.server import serve

        serve(tuple(args.directories or [Path("runs")]), args.port)
        return 0
    if args.runs_command == "view":
        from ..run_visualization import render_run_view

        candidate = load_report(args.report)
        comparison = None
        if args.baseline:
            comparison = compare_runs(
                load_report(args.baseline),
                candidate,
                changes=args.change,
                hypothesis=args.hypothesis,
            )
        elif args.change or args.hypothesis:
            raise ValueError("--change/--hypothesis require --baseline")
        rendered = render_run_view(candidate, comparison)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
        print(f"Local experiment view: {args.output}")
        return 0
    report = compare_runs(
        load_report(args.baseline),
        load_report(args.candidate),
        changes=args.change,
        hypothesis=args.hypothesis,
    )
    rendered = render_comparison(report)
    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        (args.output_dir / "comparison.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        (args.output_dir / "comparison.md").write_text(rendered, encoding="utf-8")
    print(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
        if args.output_json
        else rendered
    )
    return 0 if report["comparable"] else 1
