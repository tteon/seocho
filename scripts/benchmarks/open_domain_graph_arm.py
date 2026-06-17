#!/usr/bin/env python3
"""Open-domain graph + hybrid arm for the RAG-vs-GraphRAG replication (arXiv 2502.11371).

Fork of ``finder_4arm_sample.py`` adapted for open-domain multi-hop QA:

  - ontology arms come from ``examples/open_domain`` (``non-ontology`` ⊂
    ``generic-er`` ⊂ ``event-temporal``) — FIBO does not apply here.
  - cases come from ``open_domain_loaders`` (HotPotQA / NovelQA-proxy).
  - generator/extractor = MARA ``gpt-oss-120b`` (provider/cost policy).
  - the graph-as-context lane serves the FAIR equal-budget context: typed
    structure + the SAME top-k raw chunks the vector lane retrieves (BGE),
    NOT a query-independent dump (red-team C5/C6).
  - per-case extraction recall (gold-support coverage) is logged beside every
    graph score so a graph loss can't be mistaken for a method ceiling (C2).
  - deterministic token-F1/EM (official-style normalization) is the primary
    metric for short-answer datasets — no LLM judge for HotPotQA (guardrail #4).

Per-(dataset×model) DozerDB isolation (CLAUDE.md §13); arms isolated by
``_workspace_id``. Atomic per-(case×arm×mode) partial writes + resume guard.
"""
from __future__ import annotations

import argparse
import os
import re
import string
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "benchmarks"))

from examples.finder.lib import bench_common as bc  # noqa: E402
from examples.open_domain.ontology_modules.compose import compose_modules  # noqa: E402
from open_domain_loaders import load_dataset  # noqa: E402
from seocho.query.strategy import PromptTemplate  # noqa: E402
from seocho.store.local_embedding import LocalBGEEmbeddingBackend  # noqa: E402

# Open-domain ontology arms (nested supersets — only the ontology moves, §20.9)
ARMS: dict[str, list[str]] = {
    "non-ontology": [],
    "generic-er": ["ger"],
    "event-temporal": ["ger", "evt"],
}

PROMPT_ID = "od_kg@v1"
_INFRA_LABELS = {"Document", "DocumentVersion", "Chunk", "Section"}

ANSWER_SYSTEM = (
    "You answer a question using ONLY the provided context. Give the shortest "
    "exact answer — a name, entity, number, yes/no, or short phrase — with no "
    "explanation or restatement. If the answer is not in the context, reply "
    "exactly: not in the provided context."
)

# Generic open-domain extraction template ({{ontology}} + {{text}}).
EXTRACTION_SYSTEM = (
    "You are a knowledge-graph engineer. Extract entities and relationships from "
    "the TEXT strictly following the ONTOLOGY. Only use the entity labels and "
    "relationship types the ontology defines; do not invent labels or per-instance "
    "property keys. Keep entity `name` exactly as written in the text.\n\n"
    "{{ontology}}\n\nTEXT:\n{{text}}"
)


class KGPromptTemplate(PromptTemplate):
    """Synthesize a single composite ``{{ontology}}`` block (as finder_4arm)."""

    def render(self, context, text):  # type: ignore[override]
        ctx = dict(context)
        ctx.setdefault(
            "ontology",
            f'Ontology "{ctx.get("ontology_name", "")}":\n\n'
            f'ENTITY TYPES:\n{ctx.get("entity_types", "")}\n\n'
            f'RELATIONSHIP TYPES:\n{ctx.get("relationship_types", "")}\n\n'
            f'CONSTRAINTS:\n{ctx.get("constraints_summary", "")}',
        )
        return super().render(ctx, text)


# ---------------------------------------------------------------------------
# Deterministic metric — official SQuAD/HotPotQA-style normalization (§ guardrail #4)
# ---------------------------------------------------------------------------

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNC = str.maketrans("", "", string.punctuation)


def _normalize(s) -> str:
    s = ("" if s is None else str(s)).lower().translate(_PUNC)
    return " ".join(_ARTICLES.sub(" ", s).split())


def score_answer(gold, pred) -> dict:
    g, p = _normalize(gold), _normalize(pred)
    em = float(g == p)
    gt, pt = g.split(), p.split()
    if not gt or not pt:
        f1 = float(g == p)
        return {"em": em, "token_f1": f1, "precision": f1, "recall": f1}
    common = Counter(gt) & Counter(pt)
    ns = sum(common.values())
    if ns == 0:
        return {"em": em, "token_f1": 0.0, "precision": 0.0, "recall": 0.0}
    prec, rec = ns / len(pt), ns / len(gt)
    return {"em": em, "token_f1": round(2 * prec * rec / (prec + rec), 4),
            "precision": round(prec, 4), "recall": round(rec, 4)}


def _ontology_hash(ontology) -> str:
    try:
        ctx = ontology.to_extraction_context()
        blob = ctx.get("entity_types", "") + "\n" + ctx.get("relationship_types", "")
    except Exception:
        blob = repr(ontology)
    return bc.short_hash(blob)


# ---------------------------------------------------------------------------
# Retrieval / context
# ---------------------------------------------------------------------------

def _chunk(text: str, *, size: int, overlap: int = 100) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    out, s = [], 0
    while s < len(text):
        out.append(text[s:s + size])
        s += size - overlap
    return out


def _topk_chunks(refs: list[str], query: str, embedder, *, top_k: int, chunk_size: int) -> list[str]:
    chunks = [c for ref in refs for c in _chunk(ref, size=chunk_size)]
    if not chunks:
        return []
    cv = embedder.embed(chunks)
    qv = embedder.embed_queries([query])[0]
    scored = sorted(((sum(a * b for a, b in zip(qv, v)), i) for i, v in enumerate(cv)), reverse=True)
    return [chunks[i] for _, i in scored[:top_k]]


def _graph_structure(graph_store, ws: str, db: str) -> tuple[str, list[str]]:
    """Typed nodes + relationships for the workspace. Returns (text, node_names)."""
    lines = ["=== Knowledge graph: entities ==="]
    names: list[str] = []
    nodes = graph_store.query(
        "MATCH (n {_workspace_id:$w}) RETURN labels(n) AS l, properties(n) AS p",
        params={"w": ws}, database=db)
    for r in nodes or []:
        labs = [x for x in (r["l"] or []) if x not in _INFRA_LABELS]
        if not labs:
            continue
        nm = (r["p"] or {}).get("name") or (r["p"] or {}).get("uri") or ""
        if nm:
            names.append(str(nm))
        lines.append(f"- ({'/'.join(labs)}) {nm}")
    rels = graph_store.query(
        "MATCH (a {_workspace_id:$w})-[x]->(b {_workspace_id:$w}) "
        "RETURN coalesce(a.name,a.uri,'?') AS s, type(x) AS t, coalesce(b.name,b.uri,'?') AS o "
        "LIMIT 80", params={"w": ws}, database=db)
    if rels:
        lines.append("=== Relationships ===")
        lines.extend(f"- {r['s']} -{r['t']}-> {r['o']}" for r in rels)
    return "\n".join(lines), names


def _content_tokens(s) -> set[str]:
    """Significant (>=4 char) normalized tokens — for content-based overlap."""
    return {t for t in _normalize(s).split() if len(t) >= 4}


def _extraction_recall(node_names: list[str], gold_answer, gold_titles: list[str]) -> dict:
    """Content-based extraction-recall proxy (C2).

    Title surface forms (e.g. Wikipedia article titles) do NOT match extracted
    entity names verbatim, so a title-SUBSTRING proxy mis-reports 0.00 despite
    40+ nodes built. We use significant-token OVERLAP against the graph's
    node-name vocabulary instead:
      - ``answer_token_recall``: fraction of the gold answer's content tokens
        present among node names (can the graph even represent the answer?)
      - ``support_title_recall``: fraction of gold support titles with >=1
        content token among node names.
    """
    vocab: set[str] = set()
    for nm in node_names:
        vocab |= _content_tokens(nm)
    ans_tok = _content_tokens(gold_answer)
    ans_recall = round(len(ans_tok & vocab) / len(ans_tok), 4) if ans_tok else None
    titles = gold_titles or []
    covered = sum(1 for t in titles if (_content_tokens(t) & vocab))
    title_recall = round(covered / len(titles), 4) if titles else None
    return {"answer_token_recall": ans_recall, "support_title_recall": title_recall,
            "n_nodes": len(node_names), "n_gold_titles": len(titles),
            # legacy key kept so the summary print stays simple
            "recall": title_recall}


def _answer(llm, query: str, context: str) -> tuple[str, dict]:
    if not context.strip():
        return "not in the provided context", {}
    resp = llm.complete(system=ANSWER_SYSTEM, user=f"Question: {query}\n\n{context}")
    text = getattr(resp, "text", None) or getattr(resp, "content", None) or str(resp)
    return text, dict(getattr(resp, "usage", {}) or {})


# ---------------------------------------------------------------------------
# One (case × arm) -> graph + hybrid lane results
# ---------------------------------------------------------------------------

def run_one(*, case: dict, arm: str, modules: list[str], llm_spec: str, embedder,
            extraction_tmpl: PromptTemplate, prompt_hash: str, run_prefix: str,
            database: str, top_k: int, chunk_size: int, out_partial_dir: Path) -> list[dict]:
    from seocho import Seocho
    from seocho.store.graph import Neo4jGraphStore
    from seocho.store.llm import create_llm_backend

    ontology = compose_modules(modules)
    workspace_id = f"{run_prefix}-{arm}-{case['case_id']}"
    onto_hash = _ontology_hash(ontology)
    modules_label = "+".join(modules) or "baseline"
    provider, model = (llm_spec.split("/", 1) if "/" in llm_spec else ("mara", llm_spec))
    trace_root = f"{case['slice']}/{case['case_id']}/{arm}"
    print(f"    {trace_root}: modules={modules_label} onto={onto_hash}", flush=True)

    started = time.perf_counter()
    error = ""
    nodes_created = rels_created = 0
    add_ms = 0.0
    structure = ""
    node_names: list[str] = []
    llm = None
    client = None
    try:
        graph_store = Neo4jGraphStore(os.environ["NEO4J_URI"],
                                      os.environ.get("NEO4J_USER", "neo4j"),
                                      os.environ.get("NEO4J_PASSWORD", ""))
        llm = create_llm_backend(provider=provider.strip(), model=model.strip())
        client = Seocho(ontology=ontology, graph_store=graph_store, llm=llm,
                        workspace_id=workspace_id, extraction_prompt=extraction_tmpl)
        client.default_database = database
        try:
            graph_store.ensure_constraints(ontology, database=database)
        except Exception:
            pass
        t0 = time.perf_counter()
        for i, ref in enumerate(case["references"], 1):
            print(f"    {trace_root}: extract ref {i}/{len(case['references'])} ({len(ref)} chars)", flush=True)
            client.add(ref, user_id=workspace_id)
        add_ms = round((time.perf_counter() - t0) * 1000, 2)
        try:
            n = graph_store.query("MATCH (n {_workspace_id:$w}) RETURN count(n) AS c",
                                  params={"w": workspace_id}, database=database)
            r = graph_store.query("MATCH (a {_workspace_id:$w})-[x]->() RETURN count(x) AS c",
                                  params={"w": workspace_id}, database=database)
            nodes_created = int(n[0]["c"]) if n else 0
            rels_created = int(r[0]["c"]) if r else 0
        except Exception:
            pass
        structure, node_names = _graph_structure(graph_store, workspace_id, database)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    # Equal-budget raw chunks (BGE) — shared by graph (fair lane) and hybrid.
    raw_chunks = _topk_chunks(case["references"], case["query"], embedder,
                              top_k=top_k, chunk_size=chunk_size)
    raw_block = "\n\n---\n\n".join(f"[chunk #{i+1}]\n{c}" for i, c in enumerate(raw_chunks))
    recall = _extraction_recall(node_names, case["expected_answer"],
                                case.get("_supporting_titles") or [])

    if llm is None:
        try:
            llm = create_llm_backend(provider=provider.strip(), model=model.strip())
        except Exception:
            llm = None

    graph_ctx = f"{structure}\n\n=== Source passages (top-{top_k}) ===\n{raw_block}"
    # hybrid = same raw chunks framed as vector retrieval + graph structure (equal budget)
    hybrid_ctx = (f"=== Retrieved passages (top-{top_k}) ===\n{raw_block}\n\n"
                  f"=== Knowledge graph ===\n{structure}")
    mode_specs = [("graph", "graphrag", "graph", graph_ctx),
                  ("vector_graph", "hybrid", "vector_graph", hybrid_ctx)]

    results: list[dict] = []
    for mode_name, flow, retrieval_tag, context in mode_specs:
        tname = f"{trace_root}/{mode_name}"
        tags, metadata = bc.build_core_meta(
            dataset_name=case["category"], dataset_index=f"{case['slice']}/{case['case_id']}",
            case_id=case["case_id"], slice_tag=case["slice"], category=case["category"],
            llm_spec=llm_spec, provider=provider, mode=mode_name, reasoning_mode=False,
            flow=flow, ontology_hash=onto_hash, ontology_modules=modules_label,
            prompt_hash=prompt_hash, run_prefix=run_prefix, workspace_id=workspace_id,
            extra_tags={"ontology": arm, "retrieval": retrieval_tag, "prompt": PROMPT_ID, "seed": "42"},
            extra_metadata={"ontology_arm": arm, "nodes_created": nodes_created,
                            "relationships_created": rels_created, "extraction_recall": recall,
                            "experiment_database": database, "embedder": embedder._model_name,
                            "embedder_device": embedder.device},
        )
        ans_err, usage = "", {}
        t1 = time.perf_counter()

        def _work(c=context, _exp=case["expected_answer"]):
            ans, u = _answer(llm, case["query"], c)
            usage.update(u)
            m = score_answer(_exp, ans)
            fb = {"token_f1": m["token_f1"], "em": m["em"]}
            if u.get("prompt_tokens"):
                fb["prompt_tokens"] = u["prompt_tokens"]
            bc.set_opik_feedback_scores(fb)
            bc.set_opik_trace_metadata(name=tname, tags=tags, metadata=metadata)
            return ans

        try:
            if llm is None:
                raise RuntimeError("LLM backend unavailable")
            answer = bc.run_under_opik_track(name=tname, tags=tags, metadata=metadata, work_fn=_work)
        except Exception as exc:
            answer, ans_err = "", f"{type(exc).__name__}: {exc}"
        ask_ms = round((time.perf_counter() - t1) * 1000, 2)
        metrics = score_answer(case["expected_answer"], answer)
        result = {
            "case_id": case["case_id"], "slice": case["slice"], "category": case["category"],
            "type": case["type"], "n_refs": case["n_refs"], "arm": arm, "mode": mode_name,
            "retrieval": retrieval_tag, "ontology_modules": modules, "ontology_hash": onto_hash,
            "model": llm_spec, "prompt_id": PROMPT_ID, "prompt_hash": prompt_hash,
            "workspace_id": workspace_id, "database": database,
            "query": case["query"], "expected_answer": case["expected_answer"], "answer": answer,
            "evaluation": metrics, "extraction_recall": recall,
            "nodes_created": nodes_created, "relationships_created": rels_created,
            "graph_context_chars": len(graph_ctx), "raw_chunks_used": len(raw_chunks),
            "answer_usage": usage,
            "latency_ms": {"add": add_ms, "ask": ask_ms,
                           "total": round((time.perf_counter() - started) * 1000, 2)},
            "error": error or ans_err,
        }
        try:
            bc.atomic_write_json(
                out_partial_dir / f"{case['slice']}_{case['case_id']}_{arm}_{mode_name}.json", result)
        except Exception as exc:
            print(f"  [warn] partial write failed: {exc}", flush=True)
        results.append(result)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["hotpotqa", "novelqa"], required=True)
    ap.add_argument("--n-per-slice", type=int, default=1)
    ap.add_argument("--limit-cases", type=int, default=0)
    ap.add_argument("--arms", default="all", help="comma list or 'all'")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--llm", default=os.environ.get("SEOCHO_LLM", "mara/gpt-oss-120b"))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--chunk-size", type=int, default=800)
    ap.add_argument("--bge-model", default="BAAI/bge-small-en-v1.5")
    ap.add_argument("--bge-device", default=None,
                    help="local BGE device: auto, cuda, cpu, or mps; defaults to SEOCHO_BGE_DEVICE/auto")
    ap.add_argument("--database", default="")
    ap.add_argument("--run-prefix",
                    default=f"odgraph-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    args = ap.parse_args()

    bc.bootstrap(verbose=True)
    bc.set_global_determinism(args.seed)
    prompt_hash = bc.short_hash(EXTRACTION_SYSTEM + ANSWER_SYSTEM)

    arms = list(ARMS) if args.arms == "all" else [a.strip() for a in args.arms.split(",")]
    cases = load_dataset(args.dataset, n_per_slice=args.n_per_slice, seed=args.seed)
    if args.limit_cases:
        cases = cases[: args.limit_cases]

    # Per-(dataset×model) DB isolation (§13); arms isolated by _workspace_id.
    from seocho.store.graph import Neo4jGraphStore, sanitize_database_name
    model_short = re.sub(r"[^a-z0-9]", "", args.llm.split("/")[-1].lower())[:10]
    mmdd = datetime.now(timezone.utc).strftime("%m%d")
    database = args.database or sanitize_database_name(f"rag{mmdd}{args.dataset[:6]}{model_short}")

    print(f"== od graph arm: dataset={args.dataset} cases={len(cases)} arms={arms} "
          f"llm={args.llm} db={database} top_k={args.top_k} ==")

    embedder = LocalBGEEmbeddingBackend(model=args.bge_model, device=args.bge_device)
    print(f"== BGE {args.bge_model} dim={embedder.dim} device={embedder.device} "
          f"(hybrid/equal-budget chunks) ==")

    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    try:
        gs.ensure_database(database, wait_online=True, timeout=30.0)
    except Exception as exc:
        print(f"    [warn] ensure_database({database}): {exc}", flush=True)
    finally:
        try:
            gs.close()
        except Exception:
            pass

    extraction_tmpl = KGPromptTemplate(EXTRACTION_SYSTEM)
    out_dir = ROOT / "outputs" / "evaluation" / "open_domain_graph" / args.run_prefix
    out_partial = out_dir / "partial"
    out_partial.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    started = time.perf_counter()
    for i, case in enumerate(cases, 1):
        for arm in arms:
            print(f"\n>>> [{i}/{len(cases)}] {case['slice']} {case['case_id']} arm={arm}")
            # resume guard: skip (case×arm×mode) whose partial exists w/ matching prompt_hash
            done = all((out_partial / f"{case['slice']}_{case['case_id']}_{arm}_{m}.json").is_file()
                       for m in ("graph", "vector_graph"))
            if done:
                print("    SKIP (partials present)")
                continue
            results.extend(run_one(
                case=case, arm=arm, modules=ARMS[arm], llm_spec=args.llm, embedder=embedder,
                extraction_tmpl=extraction_tmpl, prompt_hash=prompt_hash, run_prefix=args.run_prefix,
                database=database, top_k=args.top_k, chunk_size=args.chunk_size,
                out_partial_dir=out_partial))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "run_prefix": args.run_prefix,
        "dataset": args.dataset, "llm": args.llm, "seed": args.seed, "arms": arms,
        "database": database, "embedder": args.bge_model, "embedder_device": embedder.device,
        "top_k": args.top_k,
        "prompt_id": PROMPT_ID, "prompt_hash": prompt_hash,
        "total_runs": len(results), "total_wall_seconds": round(time.perf_counter() - started, 2),
        "results": results,
    }
    agg = out_dir / "aggregate.json"
    bc.atomic_write_json(agg, payload)
    print(f"\n== wrote {agg.relative_to(ROOT)} ==")
    print("\narm             | mode         | token_f1 | em  | ans_rec | sup_rec | nodes | err")
    print("-" * 88)
    for r in results:
        er = r["extraction_recall"]
        ar = er.get("answer_token_recall")
        sr = er.get("support_title_recall")
        print(f"{r['arm']:<15} | {r['mode']:<12} | {r['evaluation']['token_f1']:.3f}    | "
              f"{r['evaluation']['em']:.0f}   | {('%.2f'%ar) if ar is not None else 'n/a':>6} | "
              f"{('%.2f'%sr) if sr is not None else 'n/a':>6}  | "
              f"{r['nodes_created']:>5} | {'Y' if r['error'] else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
