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
    compare.add_argument("--json", action="store_true", dest="output_json")


def handle(args: argparse.Namespace) -> int:
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
