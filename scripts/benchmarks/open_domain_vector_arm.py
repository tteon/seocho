#!/usr/bin/env python3
"""Open-domain vector-retrieval arm (BGE / local, $0 embeddings).

Fork of ``finder_vector_arm.py`` for the RAG-vs-GraphRAG replication
(arXiv 2502.11371), with two changes:

  1. Embeddings come from the LOCAL BGE backend (``LocalBGEEmbeddingBackend``,
     bge-small-en-v1.5, 384-dim, $0) — NOT OpenAI. MARA hosts no embedding
     endpoint (supports_embeddings=False), so per the provider/cost policy we
     use local BGE. The LanceDB table is named per-embedder so the 384-dim BGE
     vectors can never collide with 1536-dim OpenAI vectors.
  2. Cases come from ``open_domain_loaders`` (HotPotQA / NovelQA-proxy) instead
     of the FinDER CSV.

Lanes:
  ``--smoke``  : $0 path — chunk -> BGE-embed -> LanceDB -> top-k retrieve,
                 asserting dim + non-empty ranking. NO LLM call.
  (default)    : paid vector RAG — retrieve top-k then answer with the generator
                 LLM, scored with the SAME ANSWER_SYSTEM + token-F1/EM as the
                 graph arm (``open_domain_graph_arm``) so the lanes are
                 directly comparable on identical cases (§20.3). ``retrieval:
                 vector``, ``ontology:n-a``.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "benchmarks"))

from examples.finder.lib import bench_common as bc  # noqa: E402
from open_domain_loaders import load_dataset  # noqa: E402
# Reuse the EXACT answer prompt + metric from the graph arm so lanes are comparable.
from open_domain_graph_arm import ANSWER_SYSTEM, score_answer  # noqa: E402
from seocho.store.local_embedding import LocalBGEEmbeddingBackend  # noqa: E402

LANCEDB_DIR = ROOT / ".seocho" / "lancedb"
PROMPT_ID = "vector_qa@v1"


def chunk_text(text: str, *, size: int = 800, overlap: int = 100) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + size])
        start += size - overlap
    return chunks


def build_lancedb(cases: list[dict], embedder, *, table_name: str, chunk_size: int):
    """Embed every case's reference chunks once (BGE, $0) and persist to LanceDB."""
    import lancedb
    records = []
    for case in cases:
        cidx = 0
        for ref_idx, ref in enumerate(case["references"]):
            for ch in chunk_text(ref, size=chunk_size):
                records.append({
                    "id": f"{case['case_id']}::{ref_idx}::{cidx}",
                    "case_id": str(case["case_id"]), "slice": case["slice"],
                    "category": case["category"], "type": case["type"],
                    "ontology_modules": "n-a", "ref_idx": ref_idx, "chunk_idx": cidx,
                    "n_chars": len(ch), "text": ch,
                    "embed_model": embedder._model_name,
                })
                cidx += 1
    print(f"== embedding {len(records)} chunks ({embedder._model_name}, dim={embedder.dim}) "
          f"for {len(cases)} cases ==", flush=True)
    # passages: plain embed (BGE query-instruction is for QUERIES only)
    texts = [r["text"] for r in records]
    vecs = []
    for i in range(0, len(texts), 256):
        vecs.extend(embedder.embed(texts[i:i + 256]))
    assert all(len(v) == embedder.dim for v in vecs), "embedding dim mismatch"
    for r, v in zip(records, vecs):
        r["vector"] = v
    LANCEDB_DIR.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(LANCEDB_DIR))
    if table_name in db.table_names():
        db.drop_table(table_name)
    table = db.create_table(table_name, data=records)
    print(f"== LanceDB '{table_name}': {table.count_rows()} rows ==", flush=True)
    return table


def retrieve(table, case_id: str, query: str, embedder, *, top_k: int):
    qvec = embedder.embed_queries([query])[0]
    hits = table.search(qvec).where(f"case_id = '{case_id}'").limit(top_k).to_list()
    ctx = "\n\n---\n\n".join(
        f"[chunk #{i+1} d={h.get('_distance', 0):.3f}]\n{h['text']}" for i, h in enumerate(hits))
    return ctx, hits


def _smoke(cases, embedder, table, top_k) -> int:
    print("\nslice                   | case                      | hits | top-d | gold-in-top1")
    print("-" * 92)
    n_nonempty = 0
    for case in cases:
        ctx, hits = retrieve(table, str(case["case_id"]), case["query"], embedder, top_k=top_k)
        if hits:
            n_nonempty += 1
        gold = str(case["expected_answer"]).lower()
        gold_in_top1 = bool(hits) and gold[:40] in hits[0]["text"].lower()
        top_d = f"{hits[0].get('_distance', 0):.3f}" if hits else "n/a"
        print(f"{case['slice']:<23} | {str(case['case_id'])[:25]:<25} | {len(hits):>4} | "
              f"{top_d:>5} | {gold_in_top1}")
    print("-" * 92)
    print(f"== SMOKE OK: {n_nonempty}/{len(cases)} retrieved non-empty, dim={embedder.dim}, "
          f"device={embedder.device}, $0 ==")
    return 0


def run_answer(cases, embedder, table, *, llm_spec, top_k, run_prefix, out_partial) -> list[dict]:
    from seocho.store.llm import create_llm_backend
    provider, model = (llm_spec.split("/", 1) if "/" in llm_spec else ("mara", llm_spec))
    llm = create_llm_backend(provider=provider.strip(), model=model.strip())
    results: list[dict] = []
    for i, case in enumerate(cases, 1):
        partial = out_partial / f"{case['slice']}_{case['case_id']}_vector.json"
        if partial.is_file():
            print(f">>> [{i}/{len(cases)}] {case['slice']} {case['case_id']} (vector) SKIP")
            continue
        print(f">>> [{i}/{len(cases)}] {case['slice']} {case['case_id']} (vector)")
        tname = f"{case['slice']}/{case['case_id']}/vector"
        tags, metadata = bc.build_core_meta(
            dataset_name=case["category"], dataset_index=f"{case['slice']}/{case['case_id']}",
            case_id=case["case_id"], slice_tag=case["slice"], category=case["category"],
            llm_spec=llm_spec, provider=provider, mode="vector", reasoning_mode=False,
            flow="vector_rag", ontology_modules="n-a", prompt_hash=bc.short_hash(ANSWER_SYSTEM),
            run_prefix=run_prefix,
            extra_tags={"ontology": "n-a", "retrieval": "vector", "prompt": PROMPT_ID, "seed": "42"},
            extra_metadata={"embedder": embedder._model_name, "embedder_device": embedder.device,
                            "top_k": top_k, "vector_store": "lancedb"})
        answer, err, usage, retrieved = "", "", {}, 0
        t1 = time.perf_counter()
        try:
            def _work():
                nonlocal answer, usage, retrieved
                ctx, hits = retrieve(table, str(case["case_id"]), case["query"], embedder, top_k=top_k)
                retrieved = len(hits)
                if not ctx.strip():
                    answer = "not in the provided context"
                else:
                    resp = llm.complete(system=ANSWER_SYSTEM, user=f"Question: {case['query']}\n\n{ctx}")
                    answer = getattr(resp, "text", None) or getattr(resp, "content", None) or str(resp)
                    usage.update(dict(getattr(resp, "usage", {}) or {}))
                m = score_answer(case["expected_answer"], answer)
                fb = {"token_f1": m["token_f1"], "em": m["em"]}
                if usage.get("prompt_tokens"):
                    fb["prompt_tokens"] = usage["prompt_tokens"]
                bc.set_opik_feedback_scores(fb)
                bc.set_opik_trace_metadata(name=tname, tags=tags, metadata=metadata)
                return answer
            answer = bc.run_under_opik_track(name=tname, tags=tags, metadata=metadata, work_fn=_work)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        metrics = score_answer(case["expected_answer"], answer)
        result = {
            "case_id": case["case_id"], "slice": case["slice"], "category": case["category"],
            "type": case["type"], "n_refs": case["n_refs"], "arm": "vector", "mode": "vector",
            "retrieval": "vector", "model": llm_spec, "prompt_id": PROMPT_ID,
            "query": case["query"], "expected_answer": case["expected_answer"], "answer": answer,
            "evaluation": metrics, "chunks_retrieved": retrieved, "answer_usage": usage,
            "latency_ms": {"total": round((time.perf_counter() - t1) * 1000, 2)}, "error": err,
        }
        try:
            bc.atomic_write_json(partial, result)
        except Exception as exc:
            print(f"  [warn] partial write failed: {exc}", flush=True)
        ev = metrics
        print(f"    {'OK' if not err else 'ERR'} f1={ev['token_f1']:.2f} em={ev['em']:.0f} "
              f"retrieved={retrieved}")
        results.append(result)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["hotpotqa", "novelqa"], required=True)
    ap.add_argument("--n-per-slice", type=int, default=1)
    ap.add_argument("--limit-cases", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--chunk-size", type=int, default=800)
    ap.add_argument("--bge-model", default="BAAI/bge-small-en-v1.5")
    ap.add_argument("--bge-device", default=None,
                    help="local BGE device: auto, cuda, cpu, or mps; defaults to SEOCHO_BGE_DEVICE/auto")
    ap.add_argument("--llm", default=os.environ.get("SEOCHO_LLM", "mara/gpt-oss-120b"))
    ap.add_argument("--run-prefix",
                    default=f"odvec-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    ap.add_argument("--smoke", action="store_true",
                    help="$0 path: chunk->embed->retrieve only, NO LLM answer call")
    args = ap.parse_args()

    bc.bootstrap(verbose=not args.smoke)
    bc.set_global_determinism(args.seed)
    cases = load_dataset(args.dataset, n_per_slice=args.n_per_slice, seed=args.seed)
    if args.limit_cases:
        cases = cases[: args.limit_cases]
    print(f"== vector arm: dataset={args.dataset} cases={len(cases)} seed={args.seed} "
          f"top_k={args.top_k} chunk={args.chunk_size} smoke={args.smoke} ==")

    t0 = time.perf_counter()
    embedder = LocalBGEEmbeddingBackend(model=args.bge_model, device=args.bge_device)
    print(f"== BGE {args.bge_model} dim={embedder.dim} device={embedder.device} "
          f"({time.perf_counter()-t0:.1f}s) ==")
    assert embedder.dim == 384, f"expected bge-small dim 384, got {embedder.dim}"

    table_name = f"od_{args.dataset}_bge{embedder.dim}"
    table = build_lancedb(cases, embedder, table_name=table_name, chunk_size=args.chunk_size)

    if args.smoke:
        return _smoke(cases, embedder, table, args.top_k)

    out_dir = ROOT / "outputs" / "evaluation" / "open_domain_vector" / args.run_prefix
    out_partial = out_dir / "partial"
    out_partial.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    results = run_answer(cases, embedder, table, llm_spec=args.llm, top_k=args.top_k,
                         run_prefix=args.run_prefix, out_partial=out_partial)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "run_prefix": args.run_prefix,
        "dataset": args.dataset, "llm": args.llm, "seed": args.seed, "arm": "vector",
        "embedder": args.bge_model, "embedder_device": embedder.device, "top_k": args.top_k,
        "prompt_id": PROMPT_ID, "total_runs": len(results),
        "total_wall_seconds": round(time.perf_counter() - started, 2), "results": results,
    }
    agg = out_dir / "aggregate.json"
    bc.atomic_write_json(agg, payload)
    print(f"\n== wrote {agg.relative_to(ROOT)} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
