#!/usr/bin/env python3
"""FinDER vector-retrieval arm — LanceDB-persisted baseline for vector-vs-graph.

Runs on the SAME data as the graph 4-arm runner (``finder_4arm_sample.py``):
identical stratified sample (10/slice, seed 42), identical gold reference
passages, identical query, identical LLM (grok-4.3 plain chat completion), and
the identical number-aware metric. Only the retrieval differs — here, dense
vectors over the case's evidence chunks.

Symmetry with the graph lane: the graph is persisted in DozerDB, so the vector
embeddings are persisted in **LanceDB** (table ``finder_vector_0530`` under
``.seocho/lancedb``) instead of being computed in-memory and discarded. The
table is inspectable and reusable across runs.

Per case:
  - chunk the gold references, embed with OpenAI text-embedding-3-small (1536d),
    upsert into LanceDB with case/slice provenance metadata
  - vector-search the case's chunks for the query, take top-k as context
  - answer with grok-4.3 grounded ONLY in the retrieved context
  - score with the shared number-aware evaluate_answer
  - emit an Opik trace: retrieval:vector, ontology:n-a (CLAUDE.md §19)

Outputs:
  .seocho/lancedb/finder_vector_0530.lance         (persisted embeddings)
  outputs/evaluation/finder_vector_arm/<run_prefix>/aggregate.json
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for path in (SRC, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
sys.path.insert(0, str(ROOT / "scripts" / "benchmarks"))

from examples.finder.lib import bench_common as bc  # noqa: E402

# Reuse the EXACT sampling + metric from the graph runner so both lanes see
# identical cases and are scored identically.
from finder_4arm_sample import load_sample, evaluate_answer  # noqa: E402

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
PROMPT_ID = "vector_qa@v1"
LANCEDB_DIR = ROOT / ".seocho" / "lancedb"
DEFAULT_TABLE = "finder_vector_0530"

ANSWER_SYSTEM = (
    "You are a financial analyst answering a question using ONLY the provided "
    "evidence context (excerpts from SEC 10-K filings). Answer directly as a "
    "single chat completion — no reasoning narration.\n"
    "- Ground every figure in the context; preserve units, scale (thousands/"
    "millions), period (FY/quarter), and basis (GAAP/non-GAAP).\n"
    "- Show the arithmetic explicitly for any growth/ratio/delta.\n"
    "- If the needed figure is not in the context, say 'not in the provided "
    "context' rather than guessing."
)


def chunk_text(text: str, *, size: int = 800, overlap: int = 100) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return chunks


def _embed(texts: list[str], client) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):
        resp = client.embeddings.create(model=EMBED_MODEL, input=texts[i : i + 64])
        out.extend(d.embedding for d in resp.data)
    return out


def build_lancedb(cases: list[dict], oai_client, *, table_name: str, chunk_size: int):
    """Embed every case's reference chunks once and persist to LanceDB."""
    import lancedb

    records = []
    for case in cases:
        cidx = 0
        for ref_idx, ref in enumerate(case["references"]):
            for ch in chunk_text(ref, size=chunk_size):
                records.append(
                    {
                        "id": f"{case['case_id']}::{ref_idx}::{cidx}",
                        "case_id": case["case_id"],
                        "slice": case["slice"],
                        "category": case["category"],
                        "type": case["type"],
                        "ontology_modules": "n-a",
                        "ref_idx": ref_idx,
                        "chunk_idx": cidx,
                        "n_chars": len(ch),
                        "query": case["query"],
                        "expected_answer": str(case["expected_answer"]),
                        "text": ch,
                        "embed_model": EMBED_MODEL,
                    }
                )
                cidx += 1
    print(
        f"== embedding {len(records)} chunks ({EMBED_MODEL}) for {len(cases)} cases ==",
        flush=True,
    )
    vecs = _embed([r["text"] for r in records], oai_client)
    for r, v in zip(records, vecs):
        r["vector"] = v
    LANCEDB_DIR.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(LANCEDB_DIR))
    if table_name in db.table_names():
        db.drop_table(table_name)
    table = db.create_table(table_name, data=records)
    print(
        f"== LanceDB table '{table_name}' built: {table.count_rows()} rows at "
        f"{(LANCEDB_DIR / (table_name + '.lance')).relative_to(ROOT)} ==",
        flush=True,
    )
    return table


def retrieve(table, case_id: str, query: str, oai_client, *, top_k: int):
    qvec = _embed([query], oai_client)[0]
    hits = table.search(qvec).where(f"case_id = '{case_id}'").limit(top_k).to_list()
    ctx = "\n\n---\n\n".join(
        f"[chunk #{i+1} d={h.get('_distance', 0):.3f}]\n{h['text']}"
        for i, h in enumerate(hits)
    )
    return ctx, len(hits)


def run_one(
    *,
    case: dict,
    table,
    llm,
    oai_client,
    llm_spec: str,
    prompt_hash: str,
    top_k: int,
    run_prefix: str,
    out_partial_dir: Path,
) -> dict:
    provider, _ = llm_spec.split("/", 1) if "/" in llm_spec else ("grok", llm_spec)
    dataset_index = f"{case['slice']}/{case['case_id']}"
    trace_name = f"{case['slice']}/{case['case_id']}/vector"

    tags, metadata = bc.build_core_meta(
        dataset_name="all_slices.csv",
        dataset_index=dataset_index,
        case_id=case["case_id"],
        slice_tag=case["slice"],
        category=case["category"],
        llm_spec=llm_spec,
        provider=provider,
        mode="vector",
        retrieval_k=top_k,
        reasoning_mode=False,
        flow="vector_rag",
        ontology_modules="n-a",
        prompt_hash=prompt_hash,
        run_prefix=run_prefix,
        extra_tags={
            "retrieval": "vector",
            "ontology": "n-a",
            "prompt": PROMPT_ID,
            "seed": "42",
        },
        extra_metadata={
            "embed_model": EMBED_MODEL,
            "embed_dim": EMBED_DIM,
            "top_k": top_k,
            "vector_store": "lancedb",
            "prompt_id": PROMPT_ID,
            "case_query": case["query"],
            "case_n_refs": case["n_refs"],
            "case_type": case["type"],
        },
    )

    started = time.perf_counter()
    answer, error, retrieved = "", "", 0
    ask_ms = 0.0
    try:

        def _work():
            nonlocal answer, retrieved, ask_ms
            context, retrieved = retrieve(
                table, case["case_id"], case["query"], oai_client, top_k=top_k
            )
            if not context.strip():
                answer = "not in the provided context"
            else:
                t1 = time.perf_counter()
                resp = llm.complete(
                    system=ANSWER_SYSTEM,
                    user=f"Question: {case['query']}\n\nEvidence context:\n{context}",
                )
                ask_ms = round((time.perf_counter() - t1) * 1000, 2)
                answer = (
                    getattr(resp, "text", None)
                    or getattr(resp, "content", None)
                    or str(resp)
                )
            m = evaluate_answer(case["expected_answer"], answer)
            bc.set_opik_feedback_scores(
                {
                    "number_overlap": m["number_overlap_ratio"],
                    "contains_match": 1.0 if m["contains_match"] else 0.0,
                }
            )
            bc.set_opik_trace_metadata(name=trace_name, tags=tags, metadata=metadata)
            return answer

        answer = bc.run_under_opik_track(
            name=trace_name, tags=tags, metadata=metadata, work_fn=_work
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    metrics = evaluate_answer(case["expected_answer"], answer)
    result = {
        "case_id": case["case_id"],
        "slice": case["slice"],
        "category": case["category"],
        "type": case["type"],
        "n_refs": case["n_refs"],
        "arm": "vector",
        "mode": "vector",
        "retrieval": "vector",
        "model": llm_spec,
        "prompt_id": PROMPT_ID,
        "prompt_hash": prompt_hash,
        "embed_model": EMBED_MODEL,
        "vector_store": "lancedb",
        "top_k": top_k,
        "chunks_retrieved": retrieved,
        "query": case["query"],
        "expected_answer": str(case["expected_answer"]),
        "answer": answer,
        "evaluation": metrics,
        "latency_ms": {
            "ask": ask_ms,
            "total": round((time.perf_counter() - started) * 1000, 2),
        },
        "error": error,
    }
    try:
        bc.atomic_write_json(
            out_partial_dir / f"{case['slice']}_{case['case_id']}.json", result
        )
    except Exception as exc:
        print(f"  [warn] partial write failed: {exc}", flush=True)
    return result


def summarize(results: list[dict]) -> dict:
    by: dict[str, list[dict]] = {}
    for r in results:
        by.setdefault(r["slice"], []).append(r)
    out = {}
    for slc, runs in sorted(by.items()):
        ov = [r["evaluation"]["number_overlap_ratio"] for r in runs]
        ct = [r["evaluation"]["contains_match"] for r in runs]
        out[slc] = {
            "slice": slc,
            "arm": "vector",
            "n": len(runs),
            "number_overlap_mean": round(sum(ov) / len(ov), 3) if ov else 0.0,
            "contains_rate": round(sum(ct) / len(ct), 3) if ct else 0.0,
            "errors": sum(1 for r in runs if r["error"]),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-slice", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--llm", default=os.environ.get("SEOCHO_LLM", "grok/grok-4.3"))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--chunk-size", type=int, default=800)
    ap.add_argument("--table", default=DEFAULT_TABLE)
    ap.add_argument("--limit-cases", type=int, default=0)
    ap.add_argument(
        "--run-prefix",
        default=f"vector-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
    )
    args = ap.parse_args()

    bc.bootstrap(verbose=True)
    bc.set_global_determinism(args.seed)
    prompt_hash = bc.short_hash(ANSWER_SYSTEM)

    cases = load_sample(args.n_per_slice, args.seed)  # IDENTICAL to graph lane
    if args.limit_cases:
        cases = cases[: args.limit_cases]
    print(
        f"== vector arm (LanceDB): {len(cases)} cases, table={args.table} "
        f"embed={EMBED_MODEL} top_k={args.top_k} chunk={args.chunk_size} =="
    )

    from openai import OpenAI

    oai_client = OpenAI(timeout=60)
    table = build_lancedb(
        cases, oai_client, table_name=args.table, chunk_size=args.chunk_size
    )

    # Experiment-traces-only: do NOT enable SEOCHO's OpikBackend (it emits internal
    # sdk.extraction/sdk.query traces and wraps the LLM with track_openai →
    # chat_completion_create noise). Our traces come solely from
    # bc.run_under_opik_track (@track) + set_opik_trace_metadata.
    print(
        f"== tracing: experiment-traces-only (no SEOCHO backend) "
        f"project={os.environ.get('OPIK_PROJECT_NAME')} ws={os.environ.get('OPIK_WORKSPACE')} =="
    )

    def flush_tracing():
        try:
            import opik

            opik.flush_tracker()
        except Exception:
            pass

    from seocho.store.llm import create_llm_backend

    provider, model = args.llm.split("/", 1) if "/" in args.llm else ("grok", args.llm)
    llm = create_llm_backend(provider=provider.strip(), model=model.strip())

    out_dir = ROOT / "outputs" / "evaluation" / "finder_vector_arm" / args.run_prefix
    out_partial = out_dir / "partial"
    out_partial.mkdir(parents=True, exist_ok=True)

    results, started = [], time.perf_counter()
    for i, case in enumerate(cases, 1):
        print(f"\n>>> [{i}/{len(cases)}] {case['slice']} {case['case_id']} (vector)")
        res = run_one(
            case=case,
            table=table,
            llm=llm,
            oai_client=oai_client,
            llm_spec=args.llm,
            prompt_hash=prompt_hash,
            top_k=args.top_k,
            run_prefix=args.run_prefix,
            out_partial_dir=out_partial,
        )
        ev = res["evaluation"]
        mark = "OK" if not res["error"] else "ERR"
        print(
            f"    {mark}  overlap={ev['number_overlap_ratio']:.2f} "
            f"nums={ev['shared_numbers']}/{ev['expected_number_count']} "
            f"contains={ev['contains_match']} retrieved={res['chunks_retrieved']} ask={res['latency_ms']['ask']}ms"
        )
        if res["error"]:
            print(f"    error: {res['error']}")
        results.append(res)

    try:
        flush_tracing()
    except Exception:
        pass

    summary = summarize(results)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_prefix": args.run_prefix,
        "llm": args.llm,
        "seed": args.seed,
        "arm": "vector",
        "embed_model": EMBED_MODEL,
        "embed_dim": EMBED_DIM,
        "vector_store": "lancedb",
        "lancedb_table": args.table,
        "lancedb_dir": str(LANCEDB_DIR.relative_to(ROOT)),
        "top_k": args.top_k,
        "chunk_size": args.chunk_size,
        "n_per_slice": args.n_per_slice,
        "prompt_id": PROMPT_ID,
        "prompt_hash": prompt_hash,
        "opik_project": os.environ.get("OPIK_PROJECT_NAME", ""),
        "opik_workspace": os.environ.get("OPIK_WORKSPACE", ""),
        "total_runs": len(results),
        "total_wall_seconds": round(time.perf_counter() - started, 2),
        "summary": summary,
        "results": results,
    }
    agg = out_dir / "aggregate.json"
    bc.atomic_write_json(agg, payload)
    print(f"\n== wrote {agg.relative_to(ROOT)} ==")
    print("\nslice                   |  n | overlap | contains | err")
    print("-" * 60)
    for row in summary.values():
        print(
            f"{row['slice']:<23} | {row['n']:2d} | {row['number_overlap_mean']:.3f}   | "
            f"{row['contains_rate']:.2f}     | {row['errors']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
