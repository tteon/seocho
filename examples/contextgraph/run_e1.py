#!/usr/bin/env python3
"""Context-Graph E1 — content(vector) vs context(graph) vs hybrid on BC3
decision-making email threads, with a general-vs-ontology extraction ablation.

Reuses SEOCHO core + the generic graph/vector helpers from finder_4arm_sample;
the DECISION domain (dataset, ontology, prompt, answer, metric) lives here, not
in core/FinDER (CLAUDE.md: FinDER informs design, must not be a dependency).

Design (corrected 2026-06-03 after E1 v1 collapsed):
  - The decision graph is per-(THREAD, arm), built ONCE — NOT per query. A BC3
    thread yields several queries (E1/E2/E3/E4) over the SAME messages, so the
    graph is shared; rebuilding per query in one DB made same-thread workspaces
    merge (UNIQUE name) and collapse. Build once per thread, reuse for its queries.
  - ONE DB for the run (proven FinDER pattern), workspace per (thread, arm).
    Distinct threads have distinct entities → no cross-thread merge.
  - Node count is logged per build so an empty graph can never masquerade as a
    measured "graph loses" result (CLAUDE.md §20).

Per query we score 5 lanes: vector | graph@general | graph@decision |
hybrid@general | hybrid@decision. Metric: token_f1 inline (number_overlap is
meaningless for narrative); partials are finder_judge-format → score with
  finder_judge.py --judge-domain decision --judge-llms openai/gpt-5.5
"""
from __future__ import annotations
import argparse, csv, os, re, sys, json, math, statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples" / "contextgraph"))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v

from scripts.benchmarks.finder_4arm_sample import _graph_context, _vector_context
from seocho.query.strategy import PromptTemplate
from seocho.store.graph import Neo4jGraphStore, sanitize_database_name
from seocho.store.llm import create_llm_backend
from seocho import Seocho
from decision_modules.compose import compose_modules, ARMS

SEP = "===EVIDENCE_BOUNDARY==="
DATA = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
PROMPT_FILE = ROOT / "examples/contextgraph/prompts/decision_meta_system_prompt.md"

_ANSWER_SYSTEM = (
    "You are a decision analyst answering a question about an email thread using "
    "ONLY the provided context. Answer directly, grounded in the context.\n"
    "- Name the participants, proposals, positions (support/oppose), and the "
    "decision/outcome explicitly.\n"
    "- Do not invent people, proposals, or decisions not in the context.\n"
    "- If the answer is not in the context, say 'not in the provided context'."
)


def _tok(s):
    import re
    return re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split()


def token_f1(pred, gold):
    from collections import Counter
    p, g = _tok(pred), _tok(gold)
    if not p or not g:
        return 0.0
    common = sum((Counter(p) & Counter(g)).values())
    if not common:
        return 0.0
    prec, rec = common / len(p), common / len(g)
    return round(2 * prec * rec / (prec + rec), 4)


def build_decision_prompt(ontology, arm: str, prompt_file=None) -> PromptTemplate:
    """general arm => no schema block; ontology arm => inject decision schema.

    prompt_file selects the meta prompt (baseline vs approach1 SHACL+SKOS, etc.)."""
    meta = Path(prompt_file or PROMPT_FILE).read_text(encoding="utf-8")
    sys_tmpl = meta[meta.find("## ROLE"):]
    if arm == "non-ontology":
        onto_block = ("(no fixed schema — extract the salient decision elements "
                      "freely: participants, proposals, positions for/against, "
                      "arguments, decisions, with who and when)")
    else:
        ctx = ontology.to_extraction_context()
        onto_block = (f'Use ONLY these node labels and relationship types.\n\n'
                      f'ENTITY TYPES:\n{ctx.get("entity_types","")}\n\n'
                      f'RELATIONSHIP TYPES:\n{ctx.get("relationship_types","")}')
    sys_tmpl = sys_tmpl.replace("{{ontology}}", onto_block)
    return PromptTemplate(system=sys_tmpl, user="Email thread to extract:\n\n{{text}}")


def _bge_vector_context(refs, query, bge, *, top_k: int = 5, chunk_size: int = 800) -> str:
    """Top-k dense retrieval using the LOCAL BGE backend ($0, no OpenAI).

    Mirrors finder_4arm_sample._vector_context (same chunking/top_k) so the
    vector lane is comparable; only the embedder differs (BGE vs OpenAI). BGE
    vectors are already L2-normalized, so cosine == dot.
    """
    chunks = []
    for ref in refs:
        t = (ref or "").strip()
        if not t:
            continue
        if len(t) <= chunk_size:
            chunks.append(t)
        else:
            s = 0
            while s < len(t):
                chunks.append(t[s:s + chunk_size]); s += chunk_size - 100
    if not chunks:
        return ""
    cv = bge.embed(chunks)
    qv = bge.embed_queries([query])[0]
    scored = sorted(((sum(a * b for a, b in zip(qv, r)), i) for i, r in enumerate(cv)), reverse=True)
    idxs = [i for _, i in scored[:top_k]]
    return "\n\n---\n\n".join(f"[chunk #{j+1}]\n{chunks[i]}" for j, i in enumerate(idxs))


def answer(llm, query, context):
    if not context.strip():
        return "not in the provided context", {}
    # Resilience (§20.2): a single LLM timeout/connection error must NOT crash the
    # whole answering loop — record the failure for that case and continue.
    try:
        r = llm.complete(system=_ANSWER_SYSTEM, user=f"Question: {query}\n\n{context}")
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {str(e)[:100]}", {"error": type(e).__name__}
    return (getattr(r, "text", "") or ""), dict(getattr(r, "usage", {}) or {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="MiniMax-M2.5")
    ap.add_argument("--provider", default="mara")
    ap.add_argument("--arms", default="non-ontology,decision")  # general vs ontology
    ap.add_argument("--threads", type=int, default=4, help="number of distinct threads to sample")
    ap.add_argument("--run", default="e1-bc3")
    ap.add_argument("--data", default=str(DATA), help="slices CSV (bc3_slices.csv / ami_slices.csv)")
    ap.add_argument("--prompt-file", default=str(PROMPT_FILE),
                    help="extraction meta prompt (baseline vs decision_meta_system_prompt_a1.md)")
    ap.add_argument("--db-prefix", default=None, help="DB name prefix (default derived from --data stem)")
    ap.add_argument("--database", default=None, help="pin exact graph DB (decouples from --model; for --reuse-graph)")
    ap.add_argument("--embed", default="bge", choices=["bge", "openai"],
                    help="vector-lane embedder: bge=local (default, $0, cost policy) | openai")
    ap.add_argument("--build-only", action="store_true",
                    help="build graphs only, skip answering (round-1: $0 CQ/SHACL metrics, judge deferred)")
    ap.add_argument("--reuse-graph", action="store_true",
                    help="reuse already-built graphs (skip re-extraction), answer only (round-2 judge)")
    ap.add_argument("--out-run", default=None,
                    help="output dir tag (default=--run); decouples partials dir from the workspace run-tag")
    args = ap.parse_args()

    data_path = Path(args.data)
    all_cases = list(csv.DictReader(open(data_path)))
    by_thread = defaultdict(list)
    for c in all_cases:
        by_thread[str(c["_id"]).split("#")[0]].append(c)
    thread_ids = list(by_thread)[: args.threads]
    arms = [a.strip() for a in args.arms.split(",") if a.strip() in ARMS]

    # DB prefix from dataset stem (bc3_slices -> cgbc3, ami_slices -> cgami) so
    # different datasets never share a graph DB (per-dataset isolation).
    # --database pins the exact graph DB (decoupled from --model). Needed for
    # --reuse-graph when the ANSWER model differs from the model that BUILT the
    # graph (e.g. graphs built with MiniMax-M2.5, answered with M2.7 because M2.5
    # is down) — otherwise the model-derived DB would point at an empty database.
    if args.database:
        db = sanitize_database_name(args.database)
    else:
        prefix = args.db_prefix or ("cg" + re.sub(r"[^a-z0-9]", "", data_path.stem.lower().replace("slices", "")))
        db = sanitize_database_name(f"{prefix}{args.model}")
    out_run = args.out_run or args.run  # decouple output dir from workspace run-tag (reuse-graph)
    out_dir = ROOT / "outputs" / "evaluation" / "contextgraph" / out_run / "partial"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Embedder for the vector lane. Default local BGE ($0, no OpenAI) per cost policy.
    bge = None
    oai = None
    if args.embed == "bge":
        from seocho.store.local_embedding import LocalBGEEmbeddingBackend
        bge = LocalBGEEmbeddingBackend()
        print(f"  [embed] local BGE {bge._model_name} dim={bge.dim} ($0, no OpenAI)")
    else:
        from openai import OpenAI
        oai = OpenAI(timeout=60)
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    gs.ensure_database(db, wait_online=True, timeout=30.0)
    llm = create_llm_backend(provider=args.provider, model=args.model)
    print(f"== E1 [{data_path.stem}] run={args.run}: model={args.model} db={db} "
          f"arms={arms} threads={len(thread_ids)} embed={args.embed} ==\n")

    summary = []
    for tid in thread_ids:
        tcases = by_thread[tid]
        refs = [x.strip() for x in str(tcases[0]["references_joined"]).split(SEP) if x.strip()]
        # build the decision graph ONCE per (thread, arm)
        gctx_by_arm = {}
        for arm in arms:
            onto = compose_modules(ARMS[arm])
            ws = f"{args.run}-{arm}-{tid}"
            client = Seocho(ontology=onto, graph_store=gs, llm=llm,
                            workspace_id=ws, extraction_prompt=build_decision_prompt(onto, arm, args.prompt_file))
            client.default_database = db
            if args.reuse_graph:
                pass  # round-2: graph already built (build-only round-1); answer only, no re-extract
            else:
                try:
                    for r in refs:
                        client.add(r, user_id=ws)
                except Exception as e:
                    print(f"  [build {tid} @{arm}] add err: {type(e).__name__}: {str(e)[:80]}")
            # NOTE: do NOT client.close() here — it closes the SHARED gs driver,
            # which silently broke every subsequent build (build 1 wrote, close
            # killed gs, builds 2..N wrote 0 nodes). gs is closed once at the end.
            try:
                gctx = _graph_context(gs, ws, db)
            except Exception:
                gctx = ""
            try:
                nrec = gs.query("MATCH (n {_workspace_id:$w}) RETURN count(n) AS c",
                                params={"w": ws}, database=db)
                nodes = nrec[0]["c"] if nrec else 0
            except Exception:
                nodes = -1
            gctx_by_arm[arm] = gctx
            print(f"  [build {tid} @{arm:<12}] nodes={nodes} gctx_chars={len(gctx)}")

        if args.build_only:
            continue  # round-1: graphs only; $0 CQ/SHACL metrics, no answer/judge cost

        for c in tcases:
            q, gold, slice_ = c["query"], c["answer"], c["slice"]
            try:
                vec_ctx = (_bge_vector_context(refs, q, bge) if bge is not None
                           else _vector_context(refs, q, oai))
            except Exception:
                vec_ctx = ""
            lanes = {}
            a, u = answer(llm, q, "=== MESSAGES (vector top-k) ===\n" + vec_ctx)
            lanes[("vector", "n-a")] = (a, len(vec_ctx), u)
            for arm in arms:
                g = gctx_by_arm.get(arm, "")
                ag, ug = answer(llm, q, "=== DECISION GRAPH ===\n" + g)
                lanes[("graph", arm)] = (ag, len(g), ug)
                ah, uh = answer(llm, q, "=== MESSAGES ===\n" + vec_ctx + "\n\n=== DECISION GRAPH ===\n" + g)
                lanes[("hybrid", arm)] = (ah, len(vec_ctx) + len(g), uh)

            for (lane, arm), (ans, ctx_chars, usage) in lanes.items():
                f1 = token_f1(ans, gold)
                rec = {"_id": f"{c['_id']}|{lane}|{arm}", "slice": slice_, "category": "Decision",
                       "query": q, "expected_answer": gold, "answer": ans,
                       "retrieval": lane, "mode": lane, "arm": arm,
                       "model": f"{args.provider}/{args.model}",
                       "evaluation": {"number_overlap_ratio": 0.0}, "token_f1": f1,
                       "context_chars": ctx_chars, "answer_usage": usage}
                (out_dir / f"{slice_}_{c['_id']}_{lane}_{arm}.json".replace("#", "_")).write_text(
                    json.dumps(rec, default=str))
                summary.append({"slice": slice_, "lane": lane, "arm": arm, "f1": f1, "chars": ctx_chars})
            print(f"    [{c['_id']} {slice_}] " + "  ".join(
                f"{l}@{a}={token_f1(v[0], gold):.2f}" for (l, a), v in lanes.items()))

    gs.close()
    print(f"\n=== E1 rollup [{data_path.stem}]: token_f1 by lane×arm (decision) ===")
    agg = defaultdict(list); chars = defaultdict(list)
    for s in summary:
        agg[(s["lane"], s["arm"])].append(s["f1"]); chars[(s["lane"], s["arm"])].append(s["chars"])
    for k in sorted(agg):
        print(f"  {k[0]:<8}@{k[1]:<12} f1={statistics.mean(agg[k]):.3f}  ctx_chars={statistics.mean(chars[k]):.0f}  n={len(agg[k])}")
    print(f"\nwrote {len(summary)} partials -> {out_dir}")
    print("judge: python scripts/benchmarks/finder_judge.py --judge-domain decision "
          f"--judge-llms openai/gpt-5.5 --inputs 'outputs/evaluation/contextgraph/{args.run}/partial/*.json' "
          f"--out outputs/evaluation/contextgraph/{args.run}_judged.json")


if __name__ == "__main__":
    main()
