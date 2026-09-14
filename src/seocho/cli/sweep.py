"""Sweep command: parser and local execution owner."""

from __future__ import annotations
import argparse
import sys
from pathlib import Path
from typing import Any


def register(subparsers: Any) -> None:
    sweep_parser = subparsers.add_parser(
        "sweep",
        help="Run one Jinja2 run-spec template across N variants and compare the outcomes",
    )
    sweep_parser.add_argument(
        "config",
        nargs="?",
        default="seocho.sweep.yaml",
        help="Sweep spec YAML (default: ./seocho.sweep.yaml)",
    )
    sweep_parser.add_argument(
        "--init",
        action="store_true",
        help="Write seocho.sweep.yaml + run.yaml.j2 templates and exit",
    )
    sweep_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render + validate every variant and run offline preflight; no LLM calls",
    )
    sweep_parser.add_argument(
        "--show-rendered",
        nargs="?",
        const="",
        default=None,
        metavar="VARIANT",
        help="Print rendered YAML (one variant, or all when no name given) and exit",
    )
    sweep_parser.add_argument(
        "--only-variant",
        action="append",
        dest="only_variants",
        default=None,
        metavar="NAME",
        help="Run a subset of variants (repeatable)",
    )
    sweep_parser.add_argument(
        "--var",
        action="append",
        dest="var_flags",
        default=None,
        metavar="KEY=VALUE",
        help="Variable override applied to ALL variants (repeatable)",
    )
    sweep_parser.add_argument(
        "--vars",
        action="append",
        dest="vars_files",
        default=None,
        metavar="FILE",
        help="Shared variables YAML file (repeatable)",
    )
    sweep_parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first failed variant (default: keep going)",
    )
    sweep_parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Sweep directory root (default: runs/<name>-<timestamp>/)",
    )
    sweep_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-index files even if unchanged",
    )
    sweep_parser.add_argument(
        "--output-json", "--json", action="store_true", help="Emit JSON"
    )


def handle(args: argparse.Namespace) -> int:
    """Run a sweep (or write the sweep + template pair with --init)."""
    if args.init:
        from ..run_template import RUN_J2_TEMPLATE, SWEEP_TEMPLATE

        sweep_target = Path(args.config)
        template_target = sweep_target.parent / "run.yaml.j2"
        for target in (sweep_target, template_target):
            if target.exists():
                print(
                    f"{target} already exists — refusing to overwrite.", file=sys.stderr
                )
                return 1
        sweep_target.write_text(SWEEP_TEMPLATE, encoding="utf-8")
        template_target.write_text(RUN_J2_TEMPLATE, encoding="utf-8")
        print(f"Sweep spec written to {sweep_target}")
        print(f"Run template written to {template_target}")
        print("Edit the variants and template, then: seocho sweep")
        return 0

    from ..e2e import run_sweep_from_config

    return run_sweep_from_config(
        args.config,
        vars_files=getattr(args, "vars_files", None),
        var_flags=getattr(args, "var_flags", None),
        dry_run=args.dry_run,
        only_variants=getattr(args, "only_variants", None),
        fail_fast=args.fail_fast,
        output_dir=args.output,
        force=args.force,
        json_output=getattr(args, "output_json", False),
        show_rendered=getattr(args, "show_rendered", None),
    )
