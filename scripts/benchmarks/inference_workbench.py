#!/usr/bin/env python3
"""Operator workbench for cost reports, bounded serving probes and schema audits."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from seocho.eval.inference_dashboard import write_dashboard
from seocho.eval.inference_finops import alerts, summarize
from seocho.eval.inference_spine import apply_cost_ledger, join_engine_spans


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open('r', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def save_report(args: argparse.Namespace, requests: Path, out: Path) -> None:
    rows = read_jsonl(requests)
    if args.spans:
        rows = join_engine_spans(rows, read_jsonl(args.spans))
    if args.cost_ledger:
        rows = apply_cost_ledger(rows, read_jsonl(args.cost_ledger))
    report = summarize(rows)
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    report["alerts"] = alerts(report, baseline)
    telemetry = json.loads(args.telemetry.read_text()) if args.telemetry else None
    (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "alerts.json").write_text(json.dumps(report["alerts"], indent=2) + "\n")
    write_dashboard(out / "index.html", report, telemetry)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser(
        "report", help="Render private request ledger and optional engine spans"
    )
    report.add_argument("--requests", type=Path, required=True)
    probe = commands.add_parser(
        "probe", help="Make bounded actual streaming requests to an existing endpoint"
    )
    serve = commands.add_parser(
        "serve-arm", help="Start, probe and stop one isolated vLLM configuration"
    )
    for command in (report, probe, serve):
        command.add_argument("--out", type=Path, required=True)
        command.add_argument("--baseline", type=Path)
        command.add_argument("--spans", type=Path)
        command.add_argument("--cost-ledger", type=Path)
        command.add_argument("--telemetry", type=Path)
    for command in (probe, serve):
        command.add_argument("--prompts", type=Path, required=True)
        for flag in ("model", "tenant", "workspace-id", "route", "configuration-id"):
            command.add_argument("--" + flag, required=True)
        command.add_argument("--max-calls", type=int, required=True)
        command.add_argument("--max-tokens", type=int, default=256)
        command.add_argument("--dcgm-url")
        command.add_argument("--api-key-env", default="INFERENCE_API_KEY")
    probe.add_argument("--base-url", required=True)
    probe.add_argument("--metrics-url")
    serve.add_argument("--revision", required=True)
    serve.add_argument("--draft-snapshot", required=True)
    serve.add_argument("--arm", required=True)
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--max-seconds", type=int, default=900)
    serve.add_argument("--startup-seconds", type=int, default=600)
    serve.add_argument("--max-model-len", type=int, default=8192)
    plan = commands.add_parser(
        "serving-plan", help="Generate eight draft/APC/chunked-prefill commands"
    )
    for flag in ("target", "revision", "draft-snapshot"):
        plan.add_argument("--" + flag, required=True)
    plan.add_argument("--speculative-tokens", type=int, default=5)
    plan.add_argument("--out", type=Path, required=True)
    schema = commands.add_parser(
        "schema", help="Read-only DozerDB/Neo4j observations; no plugin installation"
    )
    scope = schema.add_mutually_exclusive_group(required=True)
    scope.add_argument("--workspace-id")
    scope.add_argument(
        "--database-scope",
        action="store_true",
        help="Operator-only database-wide metadata",
    )
    schema.add_argument("--database", required=True)
    schema.add_argument("--sample", type=int, default=500)
    schema.add_argument("--uri-env", default="NEO4J_URI")
    schema.add_argument("--user-env", default="NEO4J_USER")
    schema.add_argument("--password-env", default="NEO4J_PASSWORD")
    schema.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output already exists; use a fresh path to preserve artifacts")
    if args.command in {"probe", "serve-arm"}:
        from contextlib import nullcontext
        from seocho.eval.inference_probe import probe as run_probe

        prompts = read_jsonl(args.prompts)
        if not prompts or len(prompts) > args.max_calls or args.max_tokens < 1:
            parser.error("Invalid probe budget")
        if args.command == "serve-arm":
            from seocho.eval.inference_serving import serve_arm

            context = serve_arm(
                target=args.model,
                draft=args.draft_snapshot,
                revision=args.revision,
                arm=args.arm,
                out=args.out / "server",
                port=args.port,
                max_seconds=args.max_seconds,
                startup_seconds=args.startup_seconds,
                max_model_len=args.max_model_len,
            )
            args.out = args.out / "probe"
            args.metrics_url = f"http://127.0.0.1:{args.port}/metrics"
        else:
            context = nullcontext(args.base_url)
        with context as base_url:
            run_probe(
                base_url=base_url,
                model=args.model,
                prompts=prompts,
                out=args.out,
                tenant=args.tenant,
                workspace_id=args.workspace_id,
                route=args.route,
                configuration_id=args.configuration_id,
                max_calls=args.max_calls,
                max_tokens=args.max_tokens,
                metrics_url=args.metrics_url,
                dcgm_url=args.dcgm_url,
                api_key=os.environ.get(args.api_key_env, "local-vllm"),
            )
        args.telemetry = args.out / "telemetry.json"
        save_report(args, args.out / "requests.jsonl", args.out)
        summary = json.loads((args.out / "summary.json").read_text())
        return int(any(r["status"] != "ok" for r in summary["requests"]))
    if args.command == "report":
        args.out.mkdir(parents=True, mode=0o700)
        save_report(args, args.requests, args.out)
        return 0
    if args.command == "serving-plan":
        from seocho.eval.inference_probe import serving_plan

        result = serving_plan(
            args.target,
            args.draft_snapshot,
            args.revision,
            speculative_tokens=args.speculative_tokens,
        )
    else:
        from neo4j import GraphDatabase, READ_ACCESS
        from seocho.connectors.schema_discovery import (
            discover_database_schema,
            discover_workspace_shape,
        )

        uri, user, password = (
            os.environ[key] for key in (args.uri_env, args.user_env, args.password_env)
        )
        with GraphDatabase.driver(
            uri, auth=(user, password), connection_timeout=15
        ) as driver:
            with driver.session(
                database=args.database, default_access_mode=READ_ACCESS
            ) as session:
                result = (
                    discover_workspace_shape(
                        session, args.workspace_id, limit=args.sample
                    )
                    if args.workspace_id
                    else discover_database_schema(session, sample=args.sample)
                )
                result["database"] = args.database
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as handle:
        os.chmod(args.out, 0o600)
        handle.write(json.dumps(result, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
