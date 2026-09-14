"""Shared CLI option contracts, independent of command handlers."""

from __future__ import annotations
import argparse

LOCAL_PROVIDERS = ("mara", "openai", "deepseek", "kimi", "grok", "qwen", "zai")


def add_client_options(
    parser: argparse.ArgumentParser,
    *,
    include_scope: bool,
    include_json: bool,
) -> None:
    parser.add_argument("--base-url", default=None, help="SEOCHO API base URL")
    parser.add_argument("--workspace-id", default=None, help="Workspace scope")
    parser.add_argument(
        "--timeout", type=float, default=None, help="HTTP timeout in seconds"
    )
    if include_scope:
        parser.add_argument("--user-id", default=None, help="User scope")
        parser.add_argument("--agent-id", default=None, help="Agent scope")
        parser.add_argument("--session-id", default=None, help="Session scope")
    if include_json:
        parser.add_argument(
            "--json", dest="output_json", action="store_true", help="Emit JSON output"
        )
