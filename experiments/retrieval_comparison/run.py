"""4-backend × 2-mode retrieval comparison (seocho-jein).

Modes (8 total):
  - standalone: faiss, lancedb, ladybug, dozerdb
  - fusion (RRF, k=60): {faiss,lancedb} × {ladybug,dozerdb}

Vector retrieval is direct embedding-similarity over chunk text.
Graph retrieval is keyword-anchored: extract candidate terms from the
question, MATCH nodes by name/id substring, rank source chunks by
entity-hit count. This keeps the comparison about storage/query
mechanics rather than NL→Cypher quality.

Subcommands:
  seed   — index the shared corpus into all 4 backends
  query  — run every (query, mode) pair, write JSONL results
  report — render a side-by-side markdown summary

A missing backend (e.g. DozerDB not reachable, lancedb not installed) is
recorded as 'skipped' for its standalone mode and any fusion mode that
needs it. The other modes still run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

EXP_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXP_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

CORPUS_PATH = EXP_DIR / "corpus.json"
QUERIES_PATH = EXP_DIR / "queries.auto.jsonl"
ONTOLOGY_PATH = REPO_ROOT / "examples" / "datasets" / "fibo_base.jsonld"
LANCEDB_URI = str(EXP_DIR / ".lancedb")
LADYBUG_PATH = str(EXP_DIR / ".ladybug.lbug")
WORKSPACE_ID = "retrievalcompare"
NEO4J_DB = "neo4j"
TOP_K = 10


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_QUOTED_RE = re.compile(r'"([^"]+)"')
_CASED_RE = re.compile(r"\b([A-Z][A-Za-z0-9.&'-]{2,})\b")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]{3,}")
_STOP = {
    "what", "which", "where", "when", "show", "tell", "about", "from", "into",
    "with", "that", "this", "these", "those", "have", "does", "did", "are",
    "was", "were", "the", "and", "for", "but", "not", "any", "all", "above",
    "below", "than", "more", "less", "how", "many", "much", "compared", "across",
    "list", "find", "their", "they", "alongside", "between", "year", "years",
    "fiscal", "operate", "operates", "operating", "mention", "mentioned",
    "appear", "appears", "reported", "reports", "facing", "face", "company",
    "companies",
}


def extract_terms(question: str) -> List[str]:
    quoted = _QUOTED_RE.findall(question or "")
    cased = _CASED_RE.findall(question or "")
    tokens = _TOKEN_RE.findall(question or "")
    seen: Dict[str, None] = {}
    for term in quoted:
        seen[term.strip()] = None
    for term in cased:
        seen[term.strip()] = None
    for tok in tokens:
        low = tok.lower()
        if low in _STOP:
            continue
        if tok.lower() == tok and len(tok) < 5:
            continue
        seen[tok] = None
    return [t for t in seen if t][:8]


def load_corpus() -> List[Dict[str, Any]]:
    with CORPUS_PATH.open() as f:
        return json.load(f)


def load_queries() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with QUERIES_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def rrf(
    ranked_lists: Dict[str, List[str]],
    *,
    k: int = 60,
) -> List[Tuple[str, float]]:
    """Python-dict RRF — preserved as the equivalence anchor for rrf_arrow."""
    scores: Dict[str, float] = {}
    for ranked in ranked_lists.values():
        for rank, ident in enumerate(ranked, start=1):
            scores[ident] = scores.get(ident, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def items_to_arrow(items: List[Dict[str, Any]], *, backend: str) -> "pa.Table":
    """Convert per-backend retrieval items into a normalized Arrow table.

    Columns: [id: string, rank: int32, score: float64, text_preview: string,
              backend: string]. ``rank`` is 1-indexed (matches RRF convention).
    """
    import pyarrow as pa

    ids = [str(it.get("id") or "") for it in items]
    n = len(ids)
    return pa.table({
        "id": ids,
        "rank": pa.array(range(1, n + 1), type=pa.int32()),
        "score": pa.array([float(it.get("score") or 0.0) for it in items], type=pa.float64()),
        "text_preview": [str(it.get("text_preview") or "") for it in items],
        "backend": [backend] * n,
    })


def rrf_arrow(
    per_backend: Dict[str, "pa.Table"],
    *,
    k: int = 60,
    top: int = TOP_K if False else 10,
) -> "pa.Table":
    """Arrow-native RRF using Polars.

    Each input table is expected to have the schema produced by
    :func:`items_to_arrow`. The fused output schema is
    ``[id, rrf_score, n_backends, backends, text_preview]`` sorted by
    rrf_score desc.

    This is a drop-in replacement for ``rrf()`` over the same inputs; the
    test ``test_rrf_arrow_equivalent_to_python`` asserts identical ranking.
    """
    import polars as pl

    if not per_backend:
        import pyarrow as pa
        return pa.table({
            "id": pa.array([], type=pa.string()),
            "rrf_score": pa.array([], type=pa.float64()),
            "n_backends": pa.array([], type=pa.int32()),
            "backends": pa.array([], type=pa.list_(pa.string())),
            "text_preview": pa.array([], type=pa.string()),
        })

    frames = [pl.from_arrow(tbl) for tbl in per_backend.values()]
    combined = pl.concat(frames)
    return (
        combined.with_columns(
            rrf_score=(pl.lit(1.0) / (pl.lit(float(k)) + pl.col("rank").cast(pl.Float64))),
        )
        .group_by("id")
        .agg(
            rrf_score=pl.col("rrf_score").sum(),
            n_backends=pl.col("backend").n_unique().cast(pl.Int32),
            backends=pl.col("backend").unique().sort(),
            text_preview=pl.col("text_preview").first(),
        )
        .sort("rrf_score", descending=True)
        .head(top)
        .to_arrow()
    )


# ---------------------------------------------------------------------------
# Backend construction (lazy + tolerant)
# ---------------------------------------------------------------------------

def _env_neo4j() -> Tuple[str, str, str]:
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    if "://neo4j:" in uri:
        uri = uri.replace("://neo4j:", "://localhost:")
    return uri, os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password")


def open_vector_stores() -> Dict[str, Any]:
    from seocho.store.vector import create_vector_store
    stores: Dict[str, Any] = {}
    try:
        stores["faiss"] = create_vector_store(kind="faiss")
    except Exception as exc:
        stores["faiss"] = ("error", str(exc))
    try:
        stores["lancedb"] = create_vector_store(
            kind="lancedb",
            uri=LANCEDB_URI,
            table_name="retrieval_compare",
        )
    except Exception as exc:
        stores["lancedb"] = ("error", str(exc))
    return stores


def open_graph_stores() -> Dict[str, Any]:
    stores: Dict[str, Any] = {}
    try:
        from seocho.store.graph import LadybugGraphStore
        stores["ladybug"] = LadybugGraphStore(LADYBUG_PATH)
    except Exception as exc:
        stores["ladybug"] = ("error", str(exc))
    try:
        from seocho.store.graph import Neo4jGraphStore
        uri, user, pw = _env_neo4j()
        store = Neo4jGraphStore(uri, user, pw)
        # quick liveness probe
        store.query("RETURN 1 AS ok", database=NEO4J_DB)
        stores["dozerdb"] = store
    except Exception as exc:
        stores["dozerdb"] = ("error", str(exc))
    return stores


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def seed_vector(store: Any, name: str, corpus: List[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(store, tuple):
        return {"backend": name, "skipped": True, "reason": store[1]}
    if store.count() >= len(corpus):
        return {"backend": name, "skipped": True, "reason": "already_populated"}
    items = [
        {
            "id": case["id"],
            "text": case["text"],
            "metadata": {"case_id": case["id"], "category": case.get("category", "")},
        }
        for case in corpus
    ]
    t0 = time.perf_counter()
    n = store.add_batch(items)
    return {"backend": name, "added": n, "elapsed_ms": int((time.perf_counter() - t0) * 1000)}


def seed_graph(store: Any, name: str, corpus: List[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(store, tuple):
        return {"backend": name, "skipped": True, "reason": store[1]}
    from seocho import Ontology, Seocho
    from seocho.store import OpenAIBackend

    ontology = Ontology.from_jsonld(ONTOLOGY_PATH)
    try:
        store.ensure_constraints(ontology, database=NEO4J_DB)
    except Exception:
        pass
    # Probe existing node count to support idempotent re-seeding.
    try:
        existing = store.query(
            "MATCH (n) WHERE n._workspace_id = $workspace_id RETURN count(n) AS n",
            params={"workspace_id": WORKSPACE_ID},
            database=NEO4J_DB,
        )
        if existing and int(existing[0].get("n", 0)) > 0:
            return {"backend": name, "skipped": True, "reason": "already_populated"}
    except Exception:
        pass

    llm = OpenAIBackend(model="gpt-4o-mini")
    client = Seocho(
        ontology=ontology,
        graph_store=store,
        llm=llm,
        workspace_id=WORKSPACE_ID,
    )
    client.default_database = NEO4J_DB
    # Use the underlying IndexingPipeline so we can pin source_id=case_id.
    # The high-level Seocho.add() generates UUID source_ids, which prevents
    # cross-backend rank fusion (vector chunk ids would not match graph
    # source ids). Pinning source_id = case_id keeps the comparison fair.
    pipeline = client._engine._indexing  # type: ignore[attr-defined]

    t0 = time.perf_counter()
    n_added = 0
    errors: List[str] = []
    for case in corpus:
        try:
            pipeline.index(
                case["text"],
                database=NEO4J_DB,
                category=case.get("category", "memory"),
                metadata={"case_id": case["id"], "category": case.get("category", "")},
                source_id=case["id"],
            )
            n_added += 1
        except Exception as exc:
            errors.append(f"{case['id']}: {exc}")
    return {
        "backend": name,
        "added": n_added,
        "errors": errors,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def vector_retrieve(store: Any, question: str, limit: int = TOP_K) -> List[Dict[str, Any]]:
    results = store.search(question, limit=limit)
    out: List[Dict[str, Any]] = []
    for r in results:
        out.append({
            "id": r.id,
            "score": float(r.score),
            "text_preview": (r.text or "")[:140],
        })
    return out


def graph_retrieve(store: Any, question: str, limit: int = TOP_K) -> List[Dict[str, Any]]:
    terms = extract_terms(question)
    if not terms:
        return []
    cypher = (
        "MATCH (n) "
        "WHERE n._workspace_id = $workspace_id "
        "  AND (toLower(coalesce(n.name, '')) CONTAINS $term "
        "       OR toLower(coalesce(n.id, '')) CONTAINS $term) "
        "  AND coalesce(n._source_id, '') <> '' "
        "RETURN DISTINCT n._source_id AS chunk_id"
    )
    hits: Dict[str, int] = {}
    for raw in terms:
        term = raw.lower()
        try:
            rows = store.query(
                cypher,
                params={"term": term, "workspace_id": WORKSPACE_ID},
                database=NEO4J_DB,
                workspace_id=WORKSPACE_ID,
            )
        except Exception:
            continue
        for row in rows:
            cid = row.get("chunk_id")
            if cid:
                hits[cid] = hits.get(cid, 0) + 1
    ranked = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"id": cid, "score": float(h), "text_preview": ""} for cid, h in ranked]


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

@dataclass
class ModeResult:
    mode: str
    query_id: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int = 0
    error: Optional[str] = None
    skipped: bool = False
    reason: Optional[str] = None


VECTOR_BACKENDS = ("faiss", "lancedb")
GRAPH_BACKENDS = ("ladybug", "dozerdb")


def run_mode(
    mode: str,
    query_id: str,
    question: str,
    vstores: Dict[str, Any],
    gstores: Dict[str, Any],
) -> ModeResult:
    res = ModeResult(mode=mode, query_id=query_id)
    t0 = time.perf_counter()
    try:
        if mode in VECTOR_BACKENDS:
            store = vstores.get(mode)
            if isinstance(store, tuple):
                res.skipped, res.reason = True, store[1]
            else:
                res.items = vector_retrieve(store, question)
        elif mode in GRAPH_BACKENDS:
            store = gstores.get(mode)
            if isinstance(store, tuple):
                res.skipped, res.reason = True, store[1]
            else:
                res.items = graph_retrieve(store, question)
        else:
            # fusion mode like "faiss_x_dozerdb"
            v_name, g_name = mode.split("_x_")
            vstore = vstores.get(v_name)
            gstore = gstores.get(g_name)
            if isinstance(vstore, tuple) or isinstance(gstore, tuple):
                reason_parts = []
                if isinstance(vstore, tuple):
                    reason_parts.append(f"{v_name}:{vstore[1]}")
                if isinstance(gstore, tuple):
                    reason_parts.append(f"{g_name}:{gstore[1]}")
                res.skipped, res.reason = True, "; ".join(reason_parts)
            else:
                v_items = vector_retrieve(vstore, question)
                g_items = graph_retrieve(gstore, question)
                fused_tbl = rrf_arrow({
                    v_name: items_to_arrow(v_items, backend=v_name),
                    g_name: items_to_arrow(g_items, backend=g_name),
                }, top=TOP_K)
                res.items = [
                    {
                        "id": row["id"],
                        "score": float(row["rrf_score"]),
                        "text_preview": row["text_preview"],
                        "n_backends": int(row["n_backends"]),
                        "backends": list(row["backends"]),
                    }
                    for row in fused_tbl.to_pylist()
                ]
    except Exception as exc:
        res.error = repr(exc)
    res.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return res


ALL_MODES: List[str] = [
    *VECTOR_BACKENDS,
    *GRAPH_BACKENDS,
    *[f"{v}_x_{g}" for v in VECTOR_BACKENDS for g in GRAPH_BACKENDS],
]


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def cmd_seed(args: argparse.Namespace) -> int:
    corpus = load_corpus()
    print(f"[seed] corpus={len(corpus)} cases  workspace_id={WORKSPACE_ID}")
    vstores = open_vector_stores()
    gstores = open_graph_stores()

    summaries = []
    for name in VECTOR_BACKENDS:
        s = seed_vector(vstores[name], name, corpus)
        summaries.append(s)
        print(f"[seed:{name}] {s}")
    for name in GRAPH_BACKENDS:
        s = seed_graph(gstores[name], name, corpus)
        summaries.append(s)
        print(f"[seed:{name}] {s}")

    out_dir = REPO_ROOT / "outputs" / "evaluation" / "retrieval_comparison"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "seed_summary.json").write_text(json.dumps(summaries, indent=2))
    print(f"[seed] wrote {out_dir / 'seed_summary.json'}")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    queries = load_queries()
    corpus = load_corpus()
    print(f"[query] queries={len(queries)}  modes={len(ALL_MODES)}")
    vstores = open_vector_stores()
    gstores = open_graph_stores()
    # FAISS is in-memory; re-seed if empty. LanceDB / Ladybug / DozerDB
    # persist across processes, so seed_* is a no-op when already populated.
    for name in VECTOR_BACKENDS:
        store = vstores.get(name)
        if isinstance(store, tuple):
            continue
        if hasattr(store, "count") and store.count() == 0:
            print(f"[query] re-seeding vector store '{name}' (in-memory was empty)")
            seed_vector(store, name, corpus)
    print(f"[query] vector_stores={ {k: 'ok' if not isinstance(v, tuple) else 'skipped' for k, v in vstores.items()} }")
    print(f"[query] graph_stores={ {k: 'ok' if not isinstance(v, tuple) else 'skipped' for k, v in gstores.items()} }")

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = REPO_ROOT / "outputs" / "evaluation" / "retrieval_comparison" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    with results_path.open("w") as f:
        for q in queries:
            for mode in ALL_MODES:
                r = run_mode(mode, q["id"], q["query"], vstores, gstores)
                f.write(json.dumps({
                    "query_id": r.query_id,
                    "query": q["query"],
                    "category": q.get("category", ""),
                    "expected_favored_mode": q.get("expected_favored_mode", ""),
                    "mode": r.mode,
                    "items": r.items,
                    "gold_chunks": q.get("gold_chunks", []),
                    "elapsed_ms": r.elapsed_ms,
                    "error": r.error,
                    "skipped": r.skipped,
                    "reason": r.reason,
                }) + "\n")
            print(f"[query] {q['id']} done")
    print(f"[query] wrote {results_path}")
    print(f"[query] latest symlink → {out_dir.parent / 'latest'}")
    latest = out_dir.parent / "latest"
    if latest.is_symlink() or latest.exists():
        try:
            latest.unlink()
        except Exception:
            pass
    try:
        latest.symlink_to(ts)
    except Exception:
        pass
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    base = REPO_ROOT / "outputs" / "evaluation" / "retrieval_comparison"
    src = Path(args.path) if args.path else base / "latest" / "results.jsonl"
    if not src.exists():
        print(f"[report] no results at {src}", file=sys.stderr)
        return 1
    rows: List[Dict[str, Any]] = []
    with src.open() as f:
        for line in f:
            rows.append(json.loads(line))

    by_query: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for r in rows:
        by_query.setdefault(r["query_id"], {})[r["mode"]] = r

    lines: List[str] = ["# Retrieval comparison report\n", f"_source: `{src}`_\n"]
    lines.append("## Per-query top-1 ids by mode\n")
    header = "| query_id | category | expected | " + " | ".join(ALL_MODES) + " |"
    sep = "|" + "|".join(["---"] * (3 + len(ALL_MODES))) + "|"
    lines.append(header)
    lines.append(sep)
    for qid, modes in by_query.items():
        any_row = next(iter(modes.values()))
        cells = []
        for m in ALL_MODES:
            r = modes.get(m, {})
            if r.get("skipped"):
                cells.append("_skip_")
            elif r.get("error"):
                cells.append("_err_")
            elif r.get("items"):
                cells.append(r["items"][0]["id"])
            else:
                cells.append("_empty_")
        lines.append(
            f"| {qid} | {any_row.get('category','')} | {any_row.get('expected_favored_mode','')} | "
            + " | ".join(cells) + " |"
        )

    lines.append("\n## Latency (ms) median per mode\n")
    from statistics import median
    by_mode_lat: Dict[str, List[int]] = {}
    by_mode_emp: Dict[str, int] = {}
    by_mode_skip: Dict[str, int] = {}
    for r in rows:
        m = r["mode"]
        if r.get("skipped"):
            by_mode_skip[m] = by_mode_skip.get(m, 0) + 1
            continue
        by_mode_lat.setdefault(m, []).append(int(r.get("elapsed_ms") or 0))
        if not r.get("items"):
            by_mode_emp[m] = by_mode_emp.get(m, 0) + 1
    lines.append("| mode | median_ms | empty_results | skipped |")
    lines.append("|---|---|---|---|")
    for m in ALL_MODES:
        lats = by_mode_lat.get(m, [])
        med = int(median(lats)) if lats else "-"
        lines.append(f"| {m} | {med} | {by_mode_emp.get(m, 0)} | {by_mode_skip.get(m, 0)} |")

    # -------------------- hit@k + MRR --------------------
    K_VALUES = (1, 3, 5, 10)

    def _hits_at_k(retrieved_ids: List[str], gold: List[str], k: int) -> int:
        gold_set = set(gold)
        if not gold_set:
            return 0
        topk = retrieved_ids[:k]
        return int(any(ident in gold_set for ident in topk))

    def _reciprocal_rank(retrieved_ids: List[str], gold: List[str]) -> float:
        gold_set = set(gold)
        if not gold_set:
            return 0.0
        for rank, ident in enumerate(retrieved_ids, start=1):
            if ident in gold_set:
                return 1.0 / rank
        return 0.0

    # Bucket rows that have non-empty gold_chunks (others = "unanswerable",
    # measured separately by whether they returned empty).
    answerable = [r for r in rows if r.get("gold_chunks")]
    unanswerable = [r for r in rows if not r.get("gold_chunks")]

    lines.append("\n## Retrieval quality (hit@k, MRR) — answerable queries only\n")
    lines.append("| mode | n | hit@1 | hit@3 | hit@5 | hit@10 | MRR |")
    lines.append("|---|---|---|---|---|---|---|")
    for m in ALL_MODES:
        scoped = [r for r in answerable if r["mode"] == m and not r.get("skipped")]
        if not scoped:
            lines.append(f"| {m} | 0 | - | - | - | - | - |")
            continue
        hits = {k: 0 for k in K_VALUES}
        rr_sum = 0.0
        for r in scoped:
            ids = [it["id"] for it in (r.get("items") or [])]
            for k in K_VALUES:
                hits[k] += _hits_at_k(ids, r["gold_chunks"], k)
            rr_sum += _reciprocal_rank(ids, r["gold_chunks"])
        n = len(scoped)
        lines.append(
            f"| {m} | {n} | {hits[1]/n:.2f} | {hits[3]/n:.2f} | {hits[5]/n:.2f} | "
            f"{hits[10]/n:.2f} | {rr_sum/n:.3f} |"
        )

    # By-category breakdown of hit@5 to see where each mode wins.
    by_cat_mode_hits: Dict[Tuple[str, str], List[int]] = {}
    for r in answerable:
        if r.get("skipped"):
            continue
        cat = r.get("category", "")
        m = r["mode"]
        ids = [it["id"] for it in (r.get("items") or [])]
        by_cat_mode_hits.setdefault((cat, m), []).append(
            _hits_at_k(ids, r["gold_chunks"], 5)
        )
    categories = sorted({cat for cat, _ in by_cat_mode_hits})
    if categories:
        lines.append("\n## hit@5 by category × mode\n")
        header = "| category | " + " | ".join(ALL_MODES) + " |"
        sep = "|" + "|".join(["---"] * (1 + len(ALL_MODES))) + "|"
        lines.append(header)
        lines.append(sep)
        for cat in categories:
            cells = []
            for m in ALL_MODES:
                hits = by_cat_mode_hits.get((cat, m), [])
                if not hits:
                    cells.append("-")
                else:
                    cells.append(f"{sum(hits)/len(hits):.2f}")
            lines.append(f"| {cat} | " + " | ".join(cells) + " |")

    # Unanswerable: a "good" mode returns empty here, not a false positive.
    if unanswerable:
        lines.append("\n## Unanswerable queries — empty-rate per mode (higher = more honest)\n")
        lines.append("| mode | n_unanswerable | empty_rate |")
        lines.append("|---|---|---|")
        for m in ALL_MODES:
            scoped = [r for r in unanswerable if r["mode"] == m and not r.get("skipped")]
            if not scoped:
                lines.append(f"| {m} | 0 | - |")
                continue
            empties = sum(1 for r in scoped if not (r.get("items") or []))
            lines.append(f"| {m} | {len(scoped)} | {empties/len(scoped):.2f} |")

    out_md = src.with_name("report.md")
    out_md.write_text("\n".join(lines))
    print(f"[report] wrote {out_md}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="4-backend retrieval comparison")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="index the shared corpus into all backends")
    sub.add_parser("query", help="run every (query, mode) pair and write JSONL")
    p_rep = sub.add_parser("report", help="render markdown report from results.jsonl")
    p_rep.add_argument("--path", default=None, help="path to results.jsonl (default: latest)")
    args = parser.parse_args()

    if args.cmd == "seed":
        return cmd_seed(args)
    if args.cmd == "query":
        return cmd_query(args)
    if args.cmd == "report":
        return cmd_report(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
