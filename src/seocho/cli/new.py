"""New command: parser and local execution owner."""

from __future__ import annotations
import argparse
from typing import Any


def register(subparsers: Any) -> None:
    new_parser = subparsers.add_parser(
        "new", help="Create a runnable SEOCHO sample project"
    )
    new_parser.add_argument(
        "path",
        nargs="?",
        default="hello-seocho",
        help="Target directory (default: ./hello-seocho)",
    )
    new_parser.add_argument(
        "--sample",
        choices=["company"],
        default="company",
        help="Sample project to create",
    )
    new_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite scaffold-owned files in the target directory",
    )


def handle(args: argparse.Namespace) -> int:
    """Create a runnable first-run project."""
    from ..scaffold import create_sample_project

    result = create_sample_project(args.path, sample=args.sample, force=args.force)
    print(f"Created SEOCHO {result.sample!r} sample at {result.path}")
    print()
    print("Files:")
    for path in result.files:
        print(f"  {path.relative_to(result.path)}")
    print()
    print("Next:")
    print(f"  cd {result.path}")
    print("  export MARA_API_KEY=...")
    print("  seocho run --dry-run")
    print("  seocho run")
    print()
    print("From a repository checkout, prefix commands with: uv run")
    return 0
