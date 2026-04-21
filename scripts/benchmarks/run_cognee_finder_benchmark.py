#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seocho.benchmarking import (  # noqa: E402
    FinDERBenchmarkRecord,
    classify_finder_scenario,
    compare_answers,
    filter_finder_cases,
    load_finder_cases,
    summarize_finder_records,
)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _default_dataset_path() -> Path:
    return ROOT / "examples" / "datasets" / "tutorial_filings_sample.json"


def _output_dir() -> Path:
    path = ROOT / "outputs" / "evaluation" / "finder_benchmark"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_summary(payload: dict[str, Any]) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = _output_dir() / f"cognee_finder_benchmark_{timestamp}.json"
    target.write_text(json.dumps(payload, indent=2))
    return target


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-") or "cognee-finder"


def _scenario_counts(cases: list[Any]) -> dict[str, int]:
    counts = {"beginner": 0, "advanced": 0}
    for case in cases:
        counts[classify_finder_scenario(case)] += 1
    return counts


def _extract_answer(results: object) -> str:
    if isinstance(results, str):
        return results
    if isinstance(results, list):
        if not results:
            return ""
        first = results[0]
        if isinstance(first, str):
            return first
        search_result = getattr(first, "search_result", None)
        if search_result is not None:
            return str(search_result)
        return str(first)
    search_result = getattr(results, "search_result", None)
    if search_result is not None:
        return str(search_result)
    return str(results or "")


def _remember_latency_ms(remember_result: object, wall_ms: float) -> float:
    elapsed_seconds = getattr(remember_result, "elapsed_seconds", None)
    if elapsed_seconds is None:
        return round(wall_ms, 2)
    try:
        return round(float(elapsed_seconds) * 1000.0, 2)
    except (TypeError, ValueError):
        return round(wall_ms, 2)


def _prepare_cognee_roots(args: argparse.Namespace) -> dict[str, str]:
    base_root = Path(args.system_root) if args.system_root else (
        ROOT / ".seocho" / "benchmarks" / "cognee" / _slug(args.workspace_id)
    )
    if args.fresh and base_root.exists():
        shutil.rmtree(base_root)
    data_root = base_root / "data"
    system_root = base_root / "system"
    data_root.mkdir(parents=True, exist_ok=True)
    system_root.mkdir(parents=True, exist_ok=True)
    return {
        "base_root": str(base_root),
        "data_root": str(data_root),
        "system_root": str(system_root),
        "graph_db_path": str(system_root / "databases" / "cognee_graph_kuzu"),
    }


def _configure_cognee_environment(
    roots: dict[str, str],
    model: str,
    *,
    embedding_model: str,
    skip_connection_test: bool,
) -> None:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    if not api_key:
        raise SystemExit("Missing OPENAI_API_KEY/LLM_API_KEY for Cognee benchmark.")
    os.environ["LLM_PROVIDER"] = "openai"
    os.environ["EMBEDDING_PROVIDER"] = "openai"
    os.environ["LLM_MODEL"] = model
    os.environ["EMBEDDING_MODEL"] = embedding_model
    os.environ["LLM_API_KEY"] = api_key
    os.environ["OPENAI_API_KEY"] = api_key
    os.environ["EMBEDDING_API_KEY"] = api_key
    os.environ["DATA_ROOT_DIRECTORY"] = roots["data_root"]
    os.environ["SYSTEM_ROOT_DIRECTORY"] = roots["system_root"]
    os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"
    os.environ["CACHING"] = "false"
    os.environ["COGNEE_SKIP_CONNECTION_TEST"] = "true" if skip_connection_test else "false"


def _single_int_query(conn: Any, query: str) -> int:
    result = conn.execute(query)
    if not result.has_next():
        return 0
    row = result.get_next()
    if not row:
        return 0
    return int(row[0] or 0)


def _graph_counts(graph_db_path: str) -> tuple[int, int]:
    path = Path(graph_db_path)
    if not path.exists():
        return 0, 0
    import kuzu

    db = kuzu.Database(str(path))
    conn = kuzu.Connection(db)
    node_count = _single_int_query(conn, "MATCH (n) RETURN count(n)")
    edge_count = _single_int_query(conn, "MATCH ()-[r]->() RETURN count(r)")
    return node_count, edge_count


def _resolve_query_type(query_type: str | None) -> Any:
    if not query_type:
        return None
    module = importlib.import_module("cognee.modules.search.types.SearchType")
    search_type = getattr(module, "SearchType")
    return search_type[str(query_type).upper()]


async def _run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise SystemExit(
            f"FinDER dataset not found: {dataset_path}. Pass --dataset /path/to/tutorial_filings_sample.json."
        )

    roots = _prepare_cognee_roots(args)
    _configure_cognee_environment(
        roots,
        args.model,
        embedding_model=args.embedding_model,
        skip_connection_test=args.skip_connection_test,
    )

    cognee = importlib.import_module("cognee")
    version = getattr(cognee, "__version__", "unknown")
    all_cases = load_finder_cases(dataset_path)
    cases = filter_finder_cases(all_cases, args.scenario)
    if not cases:
        raise SystemExit(f"No FinDER cases matched scenario={args.scenario}.")

    query_type = _resolve_query_type(args.query_type)
    records: list[FinDERBenchmarkRecord] = []

    for case in cases:
        dataset_name = f"{_slug(args.workspace_id)}-{case.case_id}"
        answer = ""
        error = ""
        exact = False
        contains = False
        before_nodes, before_edges = _graph_counts(roots["graph_db_path"])
        add_started = time.perf_counter()
        try:
            remember_result = await cognee.remember(
                case.text,
                dataset_name=dataset_name,
                self_improvement=args.self_improvement,
            )
            add_latency_ms = _remember_latency_ms(
                remember_result,
                (time.perf_counter() - add_started) * 1000.0,
            )
            after_nodes, after_edges = _graph_counts(roots["graph_db_path"])

            ask_started = time.perf_counter()
            recall_results = await cognee.recall(
                case.question,
                datasets=[dataset_name],
                query_type=query_type,
                auto_route=query_type is None,
                top_k=args.top_k,
            )
            ask_latency_ms = round((time.perf_counter() - ask_started) * 1000.0, 2)
            answer = _extract_answer(recall_results)
            exact, contains = compare_answers(case.expected_answer, answer)
        except Exception as exc:  # pragma: no cover - live benchmark path
            add_latency_ms = round((time.perf_counter() - add_started) * 1000.0, 2)
            ask_latency_ms = 0.0
            after_nodes, after_edges = before_nodes, before_edges
            error = str(exc)
        finally:
            try:
                await cognee.forget(dataset=dataset_name)
            except Exception:
                pass

        records.append(
            FinDERBenchmarkRecord(
                case_id=case.case_id,
                category=case.category,
                add_latency_ms=round(add_latency_ms, 2),
                ask_latency_ms=round(ask_latency_ms, 2),
                answer=answer,
                expected_answer=case.expected_answer,
                exact_match=exact,
                contains_match=contains,
                nodes_created=max(0, after_nodes - before_nodes),
                relationships_created=max(0, after_edges - before_edges),
                error=error,
            )
        )

    summaries = [
        summarize_finder_records(
            mode="cognee-local-recall",
            dataset=str(dataset_path),
            records=records,
        ).to_dict()
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "workspace_id": args.workspace_id,
        "scenario": args.scenario,
        "scenario_counts": _scenario_counts(all_cases),
        "selected_case_ids": [case.case_id for case in cases],
        "cognee_setup": {
            "version": version,
            "base_root": roots["base_root"],
            "data_root": roots["data_root"],
            "system_root": roots["system_root"],
            "graph_db_path": roots["graph_db_path"],
            "query_type": args.query_type or "auto_route",
            "self_improvement": bool(args.self_improvement),
            "top_k": args.top_k,
            "embedding_model": args.embedding_model,
            "skip_connection_test": bool(args.skip_connection_test),
        },
        "summaries": summaries,
    }


def main() -> int:
    _load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Run the Cognee FinDER benchmark.")
    parser.add_argument("--dataset", default=str(_default_dataset_path()))
    parser.add_argument("--scenario", choices=("all", "beginner", "advanced"), default="all")
    parser.add_argument(
        "--workspace-id",
        default=f"cognee-finder-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
    )
    parser.add_argument("--system-root", default="")
    parser.add_argument("--query-type", default="")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"),
    )
    parser.add_argument(
        "--skip-connection-test",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--self-improvement", action="store_true")
    parser.add_argument("--fresh", action="store_true", default=True)
    args = parser.parse_args()

    payload = asyncio.run(_run_benchmark(args))
    path = _write_summary(payload)
    print(
        json.dumps(
            {
                "output_path": str(path),
                "modes": [item["mode"] for item in payload["summaries"]],
                "scenario": args.scenario,
                "selected_case_ids": payload["selected_case_ids"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
