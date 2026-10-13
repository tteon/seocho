"""FinDER pilot: original 10-K graph, fixed MARA answerer, code and native SDK coordination."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from seocho.agent.collaboration import MODES, CollaborationExperiment, Limits
from seocho.eval.finder_collaboration import (
    RECIPE_PATHS, UPSTREAM, Corpus, FinderRetriever, prepare_corpus,
    score_report, seed_corpus, verify_corpus,
)
from seocho.store.graph import Neo4jGraphStore


def download(destination: Path, remote: str) -> None:
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with urllib.request.urlopen(UPSTREAM + "/resolve/main/" + remote + "?download=true", timeout=60) as response:
        with temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
    temporary.replace(destination)


def write_report(path: Path, corpus: Corpus, reports: list[dict[str, Any]],
                 context: str, seed_seconds: float | None, *, run_limits: Limits,
                 repetitions: int, modes: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    payload = {"manifest": corpus.manifest(), "service_context": context,
               "seed_elapsed_seconds": seed_seconds, "limits": vars(run_limits),
               "repetitions": repetitions, "modes": modes, "reports": reports}
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def execute(args: argparse.Namespace, corpus: Corpus, limits: Limits) -> int:
    from seocho.recorder.adapters.openai_agents import recorded_client
    from uuid import uuid4

    if not args.recorder_url:
        raise ValueError("Live experiments require --recorder-url; direct provider bypass is disabled")

    missing = [name for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD") if not os.getenv(name)]
    if missing:
        raise ValueError("Missing environment: " + ", ".join(missing))
    store = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    model, client = recorded_client(recorder_url=args.recorder_url, model=args.model,
        workspace_id=args.workspace_id, run_id=uuid4().hex, tier='T1_RETRIEVAL',
        template_version='graphrag-collaboration-v1', timeout=args.timeout)
    reports: list[dict[str, Any]] = []
    seed_seconds = None
    try:
        if args.seed:
            started = time.monotonic()
            seed_corpus(store, corpus, workspace_id=args.workspace_id, database=args.database)
            seed_seconds = time.monotonic() - started
        verify_corpus(store, corpus, workspace_id=args.workspace_id, database=args.database)
        write_report(args.output, corpus, reports, args.service_context, seed_seconds,
                     run_limits=limits, repetitions=args.repeats, modes=args.modes)
        # Within every case/repetition, all modes share source and fresh equal limits.
        # Seeded mode shuffling reduces systematic service-warmup/order bias.
        rng = random.Random(args.selection_seed)
        for repetition in range(args.repeats):
            for item in corpus.selected:
                mode_order = list(args.modes)
                rng.shuffle(mode_order)
                retriever = FinderRetriever(store, workspace_id=args.workspace_id,
                                            corpus_id=corpus.corpus_id, question=item.case.question,
                                            database=args.database)
                experiment = CollaborationExperiment(
                    model=model, retrieve=retriever, retrieve_search=retriever.search,
                    recipes=RECIPE_PATHS, workspace_id=args.workspace_id, limits=limits, json_object_mode=True,
                )
                for order, mode in enumerate(mode_order):
                    recorder_run_id = uuid4().hex
                    experiment.model = model.for_run(recorder_run_id, mode)
                    result = await experiment.run(**item.public_payload(), mode=mode)
                    result['recorder_run_id'] = recorder_run_id
                    result['recording_attempts'] = [a for a in model.attempts if a['run_id'] == recorder_run_id]
                    # Neither expected answer nor reference membership enters experiment.run.
                    result.update(case_id=item.case.case_id, slice=item.slice_id,
                                  category=item.case.category, reasoning_type=item.case.reasoning_type,
                                  repetition=repetition, mode_order=order, model=args.model,
                                  corpus_id=corpus.corpus_id,
                                  expected_answer=item.case.expected_answer,
                                  scores=score_report(result, item, corpus.gold_mapping[item.case.case_id]))
                    reports.append(result)
                    write_report(args.output, corpus, reports, args.service_context, seed_seconds,
                                 run_limits=limits, repetitions=args.repeats, modes=args.modes)
                    print(f"{item.case.case_id}/{mode}: {result['status']}; calls={result['budget']['model_calls']}", flush=True)
        return 0 if all(report["status"] == "completed" for report in reports) else 1
    finally:
        await client.close()
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    cache = Path(".seocho/datasets/finder")
    parser.add_argument("--dataset", type=Path, default=cache / "data/train-00000-of-00001.parquet")
    parser.add_argument("--documents", type=Path, default=cache / "10-k.zip")
    parser.add_argument("--download", action="store_true", help="Fetch missing official inputs into ignored local cache")
    parser.add_argument("--dry-run", action="store_true", help="Prepare source corpus and manifest; no graph or model calls")
    parser.add_argument("--seed", action="store_true", help="MERGE original filing graph once into isolated workspace")
    parser.add_argument("--workspace-id", default="finder-collaboration-lab-v1")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--recorder-url", default=os.getenv("SEOCHO_RECORDER_URL"))
    parser.add_argument("--model", default="MiniMax-M2.5")
    parser.add_argument("--per-slice", type=int, default=10)
    parser.add_argument("--selection-seed", type=int, default=42)
    parser.add_argument("--case", help="Run an eligible upstream case ID instead of the balanced pilot")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--chunk-chars", type=int, default=3500)
    parser.add_argument("--overlap-chars", type=int, default=350)
    parser.add_argument("--max-calls", type=int, default=24)
    parser.add_argument("--admission-units", type=int, default=240_000)
    parser.add_argument("--output-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--service-context", default="unverified: supply provider/DozerDB versions, hardware/limits, warmup/skips")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    parser.add_argument("--output", type=Path, default=Path("outputs/collaboration-finder") / f"{stamp}.json")
    args = parser.parse_args()
    try:
        if args.repeats < 1 or len(args.modes) != len(set(args.modes)):
            raise ValueError("repeats must be positive and modes unique")
        limits = Limits(max_calls=args.max_calls, max_admission_units=args.admission_units,
                        max_output_tokens=args.output_tokens, timeout_seconds=args.timeout,
                        concurrency=args.concurrency)
        if args.download:
            download(args.dataset, "data/train-00000-of-00001.parquet")
            download(args.documents, "10-k.zip")
        for path in (args.dataset, args.documents):
            if not path.exists():
                raise ValueError(f"FinDER input missing: {path}; use --download or explicit input paths")
        corpus = prepare_corpus(args.dataset, args.documents, per_slice=args.per_slice,
                                seed=args.selection_seed, chunk_chars=args.chunk_chars,
                                overlap_chars=args.overlap_chars, case_id=args.case)
        # Validate workspace/database even in dry-run before any seed writes.
        FinderRetriever(None, workspace_id=args.workspace_id, corpus_id=corpus.corpus_id,
                        question="preflight", database=args.database)
        if args.dry_run:
            write_report(args.output, corpus, [], args.service_context, None, run_limits=limits,
                         repetitions=args.repeats, modes=args.modes)
            counts = corpus.manifest()["slice_counts"]
            print(f"Prepared {len(corpus.selected)} FinDER cases: {counts}; "
                  f"{len(corpus.filings)} original filings, {sum(len(f.passages) for f in corpus.filings)} passages")
            print(f"Manifest: {args.output}; no graph/model calls")
            return 0
        return asyncio.run(execute(args, corpus, limits))
    except (ValueError, FileNotFoundError, ImportError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
