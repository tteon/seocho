"""Drive the real SEOCHO RAG pipeline with KV-cache attribution switched on.

This is runbook step 3: the piece that turns the harness from "the instrument
works" into "here is what graph-agentic RAG actually does to a KV cache".

It installs a `WindowRecorder` into the SDK's LLM-call seam
(`seocho.observability.set_llm_call_observer`), runs `Seocho.local(...).ask()`
over a question set, and writes one run directory that `correlate_kv.py` reads.
Every LLM call the pipeline makes — plan/text2cypher, repair, generation — lands
as a window tagged with the stage that issued it, because `StageTimer.stage`
publishes the stage name and the backend reads it at call time.

Defaults target DozerDB over Bolt — the product's serving path. `Seocho.local()`
routes to Neo4j/DozerDB only when `graph` is a Bolt URI; anything else falls back
to the embedded LadybugDB *file* store, which is in-process and never speaks
Bolt. The same script serves the H200 measurement run; only `--llm` changes.

When a vLLM metrics endpoint is reachable, each window also carries the change in
vLLM's KV counters across it, which is how offload bytes and transfer time become
per-stage numbers rather than process-wide totals.

Usage:
    # terminal 1
    scripts/serve_track/launch_vllm.sh
    # terminal 2
    python scripts/cache_probe/kv_events_probe.py --bind --out <run>/kv_events.jsonl
    # terminal 3
    python scripts/serve_track/profile_rag.py --out-dir <run>

Writes `kv_windows.jsonl`, `run_manifest.json`, and — because the KV rig and the
pipeline's own `rag.*` span tree answer different questions — `spans.jsonl`,
which carries `n_records` per retrieval. Disable with `--trace-backend none`.

Concurrency is not supported and not a limitation to fix here: KV attribution is
a containment test on wall-clock windows, so overlapping calls would make it
ambiguous. `WindowRecorder` refuses to open a second window rather than emit a
number nobody can trust.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_ROOT / "src"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"serve_track_{name}", _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kv_windows = _load("kv_windows")
vllm_metrics = _load("vllm_metrics")

# A deliberately small ontology + corpus. The point of the run is the *shape* of
# the prompts the pipeline builds, not answer quality — a 0.6B pipe-cleaner
# model cannot do the latter anyway, and pretending otherwise would put a
# meaningless accuracy number next to a meaningful cache number.
_DEMO_ONTOLOGY = {
    # The name is load-bearing: Seocho derives the target database as
    # ``{ontology.name}{graph_model}`` sanitized, so an unnamed ontology sends
    # every query at a database called "unnamedlpg" that nobody created.
    "name": "servetrack",
    "nodes": {
        "Account": {"properties": {"name": "string", "balance": "float"}},
        "Institution": {"properties": {"name": "string"}},
    },
    "relationships": {
        "TRANSFER": {"source": "Account", "target": "Account"},
        "HELD_AT": {"source": "Account", "target": "Institution"},
    },
}

_DEMO_DOCS = [
    "Account A1 held at Northbank transferred 12000 to account B2 on 3 March.",
    "Account B2 held at Southtrust transferred 11500 to account C3 on 4 March.",
    "Account C3 held at Northbank transferred 11000 to account A1 on 6 March.",
]

_DEMO_QUESTIONS = [
    "Which accounts transferred money to B2?",
    "Which institution holds account C3?",
    "Which accounts form a transfer cycle?",
]


def _build_client(args: argparse.Namespace, llm: str, api_key: Optional[str]) -> Any:
    from seocho import Ontology, Seocho

    if args.ontology:
        ontology = Ontology.from_yaml(Path(args.ontology).read_text(encoding="utf-8"))
    else:
        ontology = Ontology.from_dict(_DEMO_ONTOLOGY)

    return Seocho.local(
        ontology,
        llm=llm,
        api_key=api_key,
        graph=args.graph,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        enforcement=args.enforcement,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/serve_track/rag"))
    parser.add_argument("--llm", default="vllm/Qwen/Qwen3-0.6B",
                        help="e.g. vllm/MiniMaxAI/MiniMax-M2.7 or mara/MiniMax-M2.7")
    parser.add_argument("--api-key", default="serve-track")
    # Indexing may run on a different model than serving. A pipe-cleaner model
    # extracts nothing, leaving an empty graph and a query path with no records
    # to retrieve — which makes the cache numbers real but the retrieval shape
    # fictional. Point indexing at a capable model; the measured serving path
    # stays whatever --llm names.
    parser.add_argument("--index-llm", default=None,
                        help="model for indexing only, e.g. mara/MiniMax-M2.7 "
                             "(default: same as --llm)")
    parser.add_argument("--index-api-key", default=None)
    # Guided extraction is free to fall back to a generic `Entity` label, which
    # leaves the ontology's own labels unpopulated — every :Account query then
    # matches nothing and synthesis is handed an empty record set. Strict makes
    # the graph match the ontology the queries are written against.
    parser.add_argument("--enforcement", default="strict",
                        choices=["strict", "guided", "open"])
    parser.add_argument("--ontology", default=None, help="ontology YAML (default: demo)")
    # DozerDB over Bolt is the product's serving path, so it is the default.
    # Seocho.local() only routes to Neo4j/DozerDB when `graph` is a Bolt URI;
    # anything else (or None) falls back to the embedded LadybugDB *file* store,
    # which is in-process and never speaks Bolt.
    parser.add_argument("--graph", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASSWORD", "neo4jpassword"))
    parser.add_argument("--metrics-url", default="http://localhost:8000/metrics",
                        help="vLLM Prometheus endpoint; sampled at each window "
                             "boundary so offload bytes/time land per stage. "
                             "Pass empty to disable.")
    parser.add_argument("--questions", default=None, help="file with one question per line")
    parser.add_argument("--repeats", type=int, default=2,
                        help="passes over the question set; pass 2+ to expose reuse "
                             "between a cold and a warm pass")
    parser.add_argument("--skip-index", action="store_true",
                        help="reuse an already-populated store")
    # On by default. The KV rig measures the serving path; the rag.* span tree
    # measures what the pipeline actually retrieved, and the two answer
    # different questions. Running without it once already produced a wrong
    # diagnosis: `rag.retrieve_ctx` records `n_records`, so "retrieval returned
    # nothing" was refutable in one line and went unrefuted for want of a
    # backend. Costs one JSONL file per run.
    parser.add_argument("--trace-backend", default="jsonl",
                        choices=["jsonl", "console", "otlp", "opik", "none"])
    args = parser.parse_args()

    from seocho.observability import set_llm_call_observer

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.trace_backend != "none":
        os.environ.setdefault("SEOCHO_TRACE_BACKEND", args.trace_backend)
        os.environ.setdefault(
            "SEOCHO_TRACE_JSONL_PATH", str(args.out_dir / "spans.jsonl")
        )
        from seocho.tracing import configure_tracing_from_env

        if not configure_tracing_from_env():
            print("WARN: tracing requested but not enabled", file=sys.stderr)
    run_id = uuid.uuid4().hex[:12]
    recorder = kv_windows.WindowRecorder(
        args.out_dir / "kv_windows.jsonl",
        metrics_sampler=vllm_metrics.make_sampler(args.metrics_url or None),
    )

    questions: List[str] = (
        [q.strip() for q in Path(args.questions).read_text(encoding="utf-8").splitlines() if q.strip()]
        if args.questions
        else list(_DEMO_QUESTIONS)
    )

    client = _build_client(args, args.llm, args.api_key)

    # Indexing is *outside* the observed region on purpose: it is a different
    # workload with a different prompt shape, and mixing its windows into the
    # query numbers would misattribute blocks to stages that never served a
    # question. Profile it as its own run if you want it.
    if not args.skip_index:
        indexer = (
            _build_client(args, args.index_llm, args.index_api_key)
            if args.index_llm
            else client
        )
        for doc in _DEMO_DOCS:
            indexer.add(doc)

    set_llm_call_observer(lambda **kw: recorder.record_step(trace_id=run_id, **kw))
    answers: List[Dict[str, Any]] = []
    try:
        for pass_index in range(args.repeats):
            for question in questions:
                started = time.time()
                try:
                    text = client.ask(question)
                    error = None
                except Exception as exc:  # a failed ask still produced windows
                    text, error = "", f"{type(exc).__name__}: {exc}"
                answers.append({
                    "pass": pass_index,
                    "question": question,
                    "answer_chars": len(text or ""),
                    "wall_ms": round((time.time() - started) * 1000.0, 2),
                    "error": error,
                })
                print(f"[pass {pass_index}] {question[:60]}"
                      f"{' — ' + error if error else ''}", flush=True)
    finally:
        set_llm_call_observer(None)
        if args.trace_backend != "none":
            from seocho.tracing import flush_tracing

            flush_tracing()

    manifest = {
        "run_id": run_id,
        "llm": args.llm,
        "repeats": args.repeats,
        "questions": questions,
        "answers": answers,
    }
    (args.out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    windows = kv_windows.read_windows(args.out_dir / "kv_windows.jsonl")
    roles: Dict[str, int] = {}
    for window in windows:
        roles[window["role"]] = roles.get(window["role"], 0) + 1
    print(f"\n{len(windows)} windows across {len(roles)} stages: "
          f"{json.dumps(roles, sort_keys=True)}")
    print(f"run dir: {args.out_dir}")
    if not windows:
        print("FAIL: no LLM calls were observed — did the pipeline reach a model?",
              file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
