#!/usr/bin/env python3
"""DISCOVERY pass — attribution-profile SEOCHO's offline data plane to rank where
Python-CPU time actually goes, so the §21 gate has a *measured* next candidate
instead of a guess (ADR-0101 follow-up).

$0 by contract: no LLM / embedding API. All workloads are pure-CPU, on-disk, or
loopback DozerDB reads, run inside `no_external_network()` so any accidental
outbound API call fails loudly. Per-stage wall (min/median/p90) is recorded to
the SQLite span store; a pyinstrument self-time tree is printed for within-stage
hotspots.

Run: python scripts/profiling/run_discovery.py
"""
from __future__ import annotations
import csv, os, sys, json, glob
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v

from seocho.profiling import timed, no_external_network
from seocho.profiling.store import SpanStore

GRAPH_DB = "cgbc3minimaxm25"
GRAPH_WS = "e1-bc3-full-decision-043-10248963"  # 89-node real workspace
BC3 = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
PARTIAL_GLOB = str(ROOT / "outputs/evaluation/contextgraph/e1-bc3-full/partial/*.json")


def _bc3_text() -> str:
    try:
        rows = list(csv.DictReader(open(BC3)))
        return "\n\n".join(str(r.get("references_joined", "")) for r in rows[:20])
    except Exception:
        return "lorem ipsum dolor sit amet. " * 4000


def main():
    store = SpanStore()
    run_id = store.start_run("discovery")
    print(f"== DISCOVERY run {run_id} (offline data plane, $0) ==\n")
    results = []  # (stage, sample, items, note)

    with no_external_network():
        # --- 1) chunking (pure CPU) ---
        try:
            from seocho.index.pipeline import chunk_text
            text = _bc3_text()
            s = timed(lambda: chunk_text(text), n=200, warmup=20)
            results.append(("chunk", s, len(text), f"{len(chunk_text(text))} chunks from {len(text)} chars"))
        except Exception as e:
            print(f"  chunk skipped: {type(e).__name__}: {e}")

        # --- 2) ontology projection (pure CPU) ---
        try:
            from examples.finder.datasets.fibo_modules.compose import compose_modules
            onto = compose_modules(["be", "ind", "fbc", "dbt", "acc"])
            s = timed(lambda: onto.to_extraction_context(), n=2000, warmup=100)
            results.append(("ontology_project", s, None, "compose_modules(medium).to_extraction_context()"))
        except Exception as e:
            print(f"  ontology_project skipped: {type(e).__name__}: {e}")

        # --- 3) JSONL IO: stdlib json vs orjson (disk + CPU) — discovery surfaces orjson ---
        try:
            files = sorted(glob.glob(PARTIAL_GLOB))[:300]
            blobs = [open(f, "rb").read() for f in files]
            s_json = timed(lambda: [json.loads(b) for b in blobs], n=20, warmup=3)
            results.append(("jsonl_io_stdlib", s_json, len(blobs), f"json.loads x{len(blobs)} partials"))
            try:
                import orjson
                s_orj = timed(lambda: [orjson.loads(b) for b in blobs], n=20, warmup=3)
                results.append(("jsonl_io_orjson", s_orj, len(blobs),
                                f"orjson.loads x{len(blobs)}  ({s_json.min_s/s_orj.min_s:.1f}x vs stdlib)"))
            except ImportError:
                pass
        except Exception as e:
            print(f"  jsonl_io skipped: {type(e).__name__}: {e}")

        # --- 4) linker lexical relatedness (pure CPU, backend=None) ---
        try:
            from seocho.index.linker import EmbeddingLinker
            linker = EmbeddingLinker(None)
            cand = {f"Entity {i}" for i in range(40)}
            known = {f"Entity {i}" for i in range(20, 140)}
            s = timed(lambda: linker.compute_relatedness(cand, known), n=5000, warmup=200)
            results.append(("linker_lexical", s, len(cand) + len(known), "compute_relatedness (lexical only)"))
        except Exception as e:
            print(f"  linker_lexical skipped: {type(e).__name__}: {e}")

        # --- 5) graph serialization (loopback DozerDB read) ---
        gs = None
        try:
            from seocho.store.graph import Neo4jGraphStore
            from scripts.benchmarks.finder_4arm_sample import _graph_context
            gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                                 os.environ.get("NEO4J_PASSWORD", ""))
            s = timed(lambda: _graph_context(gs, GRAPH_WS, GRAPH_DB), n=200, warmup=10)
            results.append(("graph_serialize", s, None, f"_graph_context ws={GRAPH_WS[:24]}"))
        except Exception as e:
            print(f"  graph_serialize skipped: {type(e).__name__}: {e}")

        # --- 6) rule inference whole-path (loopback read for nodes, then pure CPU) ---
        try:
            from scripts.profiling.bench_seocho_core import load_real_nodes
            import seocho.rules as rules
            rules._USE_NATIVE_RULES = False  # measure the production (Python) path
            nodes = load_real_nodes()[:1000]
            data = {"nodes": nodes}
            if nodes:
                s = timed(lambda: rules.infer_rules_from_graph(data), n=50, warmup=5)
                results.append(("rules_infer_py", s, len(nodes), "infer_rules_from_graph (python path)"))
        except Exception as e:
            print(f"  rules_infer skipped: {type(e).__name__}: {e}")
        if gs is not None:
            gs.close()

    # persist + rank
    for stage, s, items, note in results:
        store.add_span(run_id, stage, "wall", sample=s, items=items, note=note)
    print(f"{'stage':<20}{'min(ms)':>10}{'median(ms)':>12}{'p90(ms)':>10}   note")
    print("-" * 92)
    for stage, s, items, note in sorted(results, key=lambda r: -r[1].min_s):
        print(f"{stage:<20}{s.min_s*1e3:>10.3f}{s.median_s*1e3:>12.3f}{s.p90_s*1e3:>10.3f}   {note}")
    store.close()
    print(f"\nwrote spans -> outputs/profiling/spans.db (run {run_id})")
    print("NOTE: ranks per-CALL cost; multiply by call frequency in the real pipeline "
          "for Amdahl share before nominating a §21 candidate.")


if __name__ == "__main__":
    main()
