"""Run command: parser and local execution owner."""

from __future__ import annotations
import argparse
import sys
from pathlib import Path
from typing import Any


def register(subparsers: Any) -> None:
    run_parser = subparsers.add_parser(
        "run",
        help="Run a YAML-declared e2e flow: index documents, ask questions, write a report",
    )
    run_parser.add_argument(
        "config",
        nargs="?",
        default="seocho.run.yaml",
        help="Run spec YAML (default: ./seocho.run.yaml)",
    )
    run_parser.add_argument(
        "--init",
        action="store_true",
        help="Write a commented run spec template to the config path and exit",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the config and run offline preflight checks; no LLM calls",
    )
    run_parser.add_argument(
        "--only",
        choices=["index", "query"],
        help="Run a single phase (query reuses the existing graph)",
    )
    run_parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Report directory (default: runs/<name>-<timestamp>/)",
    )
    run_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-index files even if unchanged",
    )
    run_parser.add_argument(
        "--no-track",
        action="store_true",
        help="Index every input without reading/writing .seocho_index (use for isolated experiments)",
    )
    run_parser.add_argument(
        "--var",
        action="append",
        dest="var_flags",
        default=None,
        metavar="KEY=VALUE",
        help="Template variable for *.j2 configs (repeatable; dotted keys, YAML values)",
    )
    run_parser.add_argument(
        "--vars",
        action="append",
        dest="vars_files",
        default=None,
        metavar="FILE",
        help="YAML file of template variables (repeatable; --var overrides)",
    )
    run_parser.add_argument(
        "--show-rendered",
        action="store_true",
        help="Print rendered template YAML, or the plain YAML spec, and exit",
    )
    run_parser.add_argument(
        "--output-json", "--json", action="store_true", help="Emit JSON"
    )


def handle(args: argparse.Namespace) -> int:
    """Run a YAML-declared e2e flow (or write a template with --init)."""
    if args.init:
        from ..run_spec import RUN_SPEC_TEMPLATE

        target = Path(args.config)
        if target.exists():
            print(f"{target} already exists — refusing to overwrite.", file=sys.stderr)
            return 1
        target.write_text(RUN_SPEC_TEMPLATE, encoding="utf-8")
        print(f"Run spec template written to {target}")
        print("Edit the ontology/documents/questions, then: seocho run")
        print("Need a runnable sample instead? Try: seocho new hello-seocho")
        return 0

    from ..e2e import run_from_config

    return run_from_config(
        args.config,
        dry_run=args.dry_run,
        only=args.only,
        output_dir=args.output,
        force=args.force,
        track=not args.no_track,
        json_output=getattr(args, "output_json", False),
        vars_files=getattr(args, "vars_files", None),
        var_flags=getattr(args, "var_flags", None),
        show_rendered=getattr(args, "show_rendered", False),
    )
