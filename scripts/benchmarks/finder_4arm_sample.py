#!/usr/bin/env python3
"""FinDER 4-arm ontology sample run — graph build + QA, Opik-traced.

For a stratified sample (default 10 cases/slice → ~60 cases) of
``examples/datasets/finder/all_slices.csv``, build a knowledge graph and answer the query under
each of the 4 ontology arms (CLAUDE.md §19):

    non-ontology  -> compose_modules([])                  (Entity/RELATED_TO)
    small         -> be, ind                              (financial core)
    medium        -> be, ind, fbc, dbt, acc               (Goldilocks candidate)
    large         -> all 9 FIBO modules                   (over-provisioned)

Each (case × arm) run:
  - composes the arm's ontology, builds Seocho on Neo4j/DozerDB + grok-4.3
    (plain chat completion, reasoning_mode=False)
  - extracts the case's gold reference passages into the graph using the
    xAI KG-engineer extraction template ({{ontology}} + {{text}})
  - answers the query and scores it (number-aware)
  - emits an Opik trace tagged on the 4 required axes (model, ontology, slice,
    prompt) + retrieval:graph  (CLAUDE.md §19 tagging contract)

Outputs:
  outputs/evaluation/finder_4arm_sample/<run_prefix>/aggregate.json
  outputs/evaluation/finder_4arm_sample/<run_prefix>/partial/<slice>_<case>_<arm>.json
"""
from __future__ import annotations

import argparse
import json
import math  # noqa: F401  (used by vector-context cosine)
import os
import re
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

from examples.finder.lib import bench_common as bc  # noqa: E402
from seocho.query.strategy import PromptTemplate  # noqa: E402

REF_SEPARATOR = "===EVIDENCE_BOUNDARY==="
_NUM_RE = re.compile(r"-?\$?\d[\d,]*\.?\d*(?:%| million| billion| thousand)?", re.IGNORECASE)

DATASET_CSV = ROOT / "examples" / "datasets" / "finder" / "all_slices.csv"
GROK_META_PROMPT = ROOT / "examples/finder/datasets/grok_meta_system_prompt.md"

# The 4 ontology arms (CLAUDE.md §19, confirmed nested supersets)
ARMS: dict[str, list[str]] = {
    "non-ontology": [],
    "small": ["be", "ind"],
    "medium": ["be", "ind", "fbc", "dbt", "acc"],
    "large": ["be", "ind", "fbc", "dbt", "acc", "fnd", "sec", "mkt", "corp"],
}

PROMPT_ID = "grok_kg@v1"


def _ensure_db_ready(graph_store, db_name: str, *, timeout: float = 30.0) -> None:
    """Thin caller for the SDK's online-waiting database creation.

    The wait-until-ONLINE logic now lives in
    ``Neo4jGraphStore.ensure_database(..., wait_online=True)`` (DozerDB CREATE
    DATABASE is async); this wrapper just skips the builtin ``neo4j`` db.
    """
    if db_name == "neo4j":
        return
    try:
        graph_store.ensure_database(db_name, wait_online=True, timeout=timeout)
    except Exception as exc:
        print(f"    [warn] ensure_database({db_name}) failed: {exc}", flush=True)


class KGPromptTemplate(PromptTemplate):
    """PromptTemplate that exposes a single composite ``{{ontology}}`` variable.

    SEOCHO's extraction context only provides the granular keys
    (ontology_name/entity_types/relationship_types/constraints_summary). The
    xAI KG-engineer template references ``{{ontology}}`` as one authoritative
    schema block, so we synthesize it here before the base replacement runs.
    """

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
# Data loading + sampling
# ---------------------------------------------------------------------------

def load_sample(n_per_slice: int, seed: int) -> list[dict]:
    import pandas as pd
    if not DATASET_CSV.is_file():
        raise SystemExit(f"Missing dataset CSV at {DATASET_CSV}")
    df = pd.read_csv(DATASET_CSV)
    parts = []
    for slice_tag, group in df.groupby("slice"):
        take = min(n_per_slice, len(group))
        parts.append(group.sample(n=take, random_state=seed).sort_values("_id"))
    sample = pd.concat(parts, ignore_index=True)
    cases: list[dict] = []
    for _, r in sample.iterrows():
        refs = [x.strip() for x in str(r["references_joined"]).split(REF_SEPARATOR) if x.strip()]
        cases.append({
            "case_id": r["_id"],
            "slice": r["slice"],
            "category": r["category"],
            "type": r["type"] if isinstance(r["type"], str) else "",
            "n_refs": int(r["n_refs"]),
            "query": r["query"],
            "expected_answer": r["answer"],
            "references": refs,
        })
    return cases


# ---------------------------------------------------------------------------
# Answer evaluation (number-aware, same metric across all arms — §20.3)
# ---------------------------------------------------------------------------

def _safe_str(x) -> str:
    """Coerce to str, treating NaN/None as empty (CSV answers can be NaN floats)."""
    if x is None:
        return ""
    if isinstance(x, float) and x != x:  # NaN
        return ""
    return str(x)


def _nums(text) -> set[str]:
    return {n.replace(",", "").strip().lower() for n in _NUM_RE.findall(_safe_str(text))}


def evaluate_answer(expected, actual) -> dict:
    exp_s, act_s = _safe_str(expected), _safe_str(actual)
    exp, act = _nums(exp_s), _nums(act_s)
    shared = exp & act
    return {
        "contains_match": bool(act_s) and exp_s.strip().lower() in act_s.strip().lower(),
        "shared_numbers": len(shared),
        "expected_number_count": len(exp),
        "actual_number_count": len(act),
        "number_overlap_ratio": (len(shared) / len(exp)) if exp else 0.0,
    }


def _ontology_hash(ontology) -> str:
    try:
        ctx = ontology.to_extraction_context()
        blob = ctx.get("entity_types", "") + "\n" + ctx.get("relationship_types", "")
    except Exception:
        blob = repr(ontology)
    return bc.short_hash(blob)


# ---------------------------------------------------------------------------
# One (case × arm)
# ---------------------------------------------------------------------------

_INFRA_LABELS = {"Document", "DocumentVersion", "Chunk", "Section"}
_EMBED_MODEL = "text-embedding-3-small"
_ANSWER_SYSTEM = (
    "You are a financial analyst answering a question using ONLY the provided "
    "context (derived from SEC 10-K filings). Answer directly as a single chat "
    "completion — no reasoning narration.\n"
    "- Ground every figure in the context; preserve units, scale (thousands/"
    "millions), period (FY/quarter), basis (GAAP/non-GAAP).\n"
    "- Show the arithmetic explicitly for any growth/ratio/delta.\n"
    "- If the needed figure is not in the context, say 'not in the provided "
    "context' rather than guessing."
)


def _graph_context(graph_store, ws: str, db: str) -> str:
    """Serialize the case's extracted subgraph to text (graph-as-context).

    Robust alternative to structured Cypher Q&A: dumps typed nodes (+ value/
    period/basis), relationships, and stored chunk text for the workspace so the
    LLM can read what this ontology arm actually extracted. (CLAUDE.md §19.)
    """
    lines: list[str] = ["=== Knowledge graph: entities & metrics ==="]
    nodes = graph_store.query(
        "MATCH (n {_workspace_id:$w}) RETURN labels(n) AS l, properties(n) AS p",
        params={"w": ws}, database=db)
    for r in nodes or []:
        labs = [x for x in (r["l"] or []) if x not in _INFRA_LABELS]
        if not labs:
            continue
        p = r["p"] or {}
        nm = p.get("name") or p.get("uri") or ""
        bits = [f"{k}={p[k]}" for k in
                ("value", "period", "basis", "segment", "amount", "amount_per_share",
                 "coupon_rate", "maturity_date") if p.get(k)]
        lines.append(f"- ({'/'.join(labs)}) {nm}" + (f" [{', '.join(bits)}]" if bits else ""))
    rels = graph_store.query(
        "MATCH (a {_workspace_id:$w})-[x]->(b {_workspace_id:$w}) "
        "RETURN coalesce(a.name,a.uri,'?') AS s, type(x) AS t, coalesce(b.name,b.uri,'?') AS o "
        "LIMIT 80", params={"w": ws}, database=db)
    if rels:
        lines.append("=== Relationships ===")
        for r in rels:
            lines.append(f"- {r['s']} -{r['t']}-> {r['o']}")
    return "\n".join(lines)


def _embed_texts(texts: list[str], oai_client) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):
        resp = oai_client.embeddings.create(model=_EMBED_MODEL, input=texts[i:i + 64])
        out.extend(d.embedding for d in resp.data)
    return out


def _vector_context(refs: list[str], query: str, oai_client, *, top_k: int = 5,
                    chunk_size: int = 800) -> str:
    """Top-k dense retrieval over the same gold references (vector lane context)."""
    chunks: list[str] = []
    for ref in refs:
        t = (ref or "").strip()
        if not t:
            continue
        if len(t) <= chunk_size:
            chunks.append(t)
        else:
            s = 0
            while s < len(t):
                chunks.append(t[s:s + chunk_size])
                s += chunk_size - 100
    if not chunks:
        return ""
    cv = _embed_texts(chunks, oai_client)
    qv = _embed_texts([query], oai_client)[0]
    def _norm(v):
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]
    q = _norm(qv)
    scored = sorted(((sum(a * b for a, b in zip(q, _norm(r))), i) for i, r in enumerate(cv)),
                    reverse=True)
    idxs = [i for _, i in scored[:top_k]]
    return "\n\n---\n\n".join(f"[chunk #{j+1}]\n{chunks[i]}" for j, i in enumerate(idxs))


def _grok_answer(llm, query: str, context: str) -> str:
    if not context.strip():
        return "not in the provided context"
    resp = llm.complete(system=_ANSWER_SYSTEM, user=f"Question: {query}\n\n{context}")
    return getattr(resp, "text", None) or getattr(resp, "content", None) or str(resp)


def run_one(*, case: dict, arm: str, modules: list[str], llm_spec: str,
            extraction_tmpl: PromptTemplate, prompt_hash: str, run_prefix: str,
            database: str, oai_client, out_partial_dir: Path) -> list[dict]:
    from seocho import Seocho
    from seocho.store.graph import Neo4jGraphStore
    from seocho.store.llm import create_llm_backend

    from examples.finder.datasets.fibo_modules.compose import compose_modules

    ontology = compose_modules(modules)
    workspace_id = f"{run_prefix}-{arm}-{case['case_id']}"
    trace_name = f"{case['slice']}/{case['case_id']}/{arm}"
    modules_label = "+".join(modules) or "baseline"
    onto_hash = _ontology_hash(ontology)
    dataset_index = f"{case['slice']}/{case['case_id']}"
    provider, model = (llm_spec.split("/", 1) if "/" in llm_spec else ("grok", llm_spec))

    print(f"    {trace_name}: arm={arm} modules={modules_label} onto={onto_hash}", flush=True)

    started = time.perf_counter()
    error = ""
    nodes_created = rels_created = 0
    add_ms = 0.0
    graph_ctx = vec_ctx = ""
    llm = None
    client = None
    try:
        graph_store = Neo4jGraphStore(
            os.environ["NEO4J_URI"],
            os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""),
        )
        llm = create_llm_backend(provider=provider.strip(), model=model.strip())
        client = Seocho(ontology=ontology, graph_store=graph_store, llm=llm,
                        workspace_id=workspace_id, extraction_prompt=extraction_tmpl)
        # ONE fixed experiment DB (Opik-style name); per-(case×arm) isolated by
        # _workspace_id. DB created+onlined once in main().
        client.default_database = database
        try:
            graph_store.ensure_constraints(ontology, database=database)
        except Exception:
            pass

        # Extract the gold references into the graph (ontology-guided).
        t0 = time.perf_counter()
        for i, ref in enumerate(case["references"], 1):
            print(f"    {trace_name}: add ref {i}/{len(case['references'])} ({len(ref)} chars)", flush=True)
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

        graph_ctx = _graph_context(graph_store, workspace_id, database)
        vec_ctx = _vector_context(case["references"], case["query"], oai_client)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    # Two retrieval modes that USE this arm's graph: graph-as-context and the
    # vector&graph hybrid. (Pure vector is the arm-independent lane in
    # finder_vector_arm.py.) Each mode = a separate grok call + Opik trace,
    # tagged with the ontology arm; identical metric across modes (§20.3).
    if llm is None:
        try:
            llm = create_llm_backend(provider=provider.strip(), model=model.strip())
        except Exception:
            llm = None

    mode_specs = [
        ("graph", "graphrag", "graph", "=== GRAPH CONTEXT ===\n" + graph_ctx),
        ("vector_graph", "hybrid", "vector_graph",
         "=== VECTOR CONTEXT (retrieved chunks) ===\n" + vec_ctx +
         "\n\n=== GRAPH CONTEXT ===\n" + graph_ctx),
    ]
    results: list[dict] = []
    for mode_name, flow, retrieval_tag, context in mode_specs:
        tname = f"{case['slice']}/{case['case_id']}/{arm}/{mode_name}"
        tags, metadata = bc.build_core_meta(
            dataset_name="all_slices.csv", dataset_index=dataset_index,
            case_id=case["case_id"], slice_tag=case["slice"], category=case["category"],
            llm_spec=llm_spec, provider=provider, mode=mode_name, reasoning_mode=False,
            repair_budget=0, flow=flow, ontology_hash=onto_hash, ontology_modules=modules_label,
            prompt_hash=prompt_hash, run_prefix=run_prefix, workspace_id=workspace_id,
            extra_tags={"ontology": arm, "retrieval": retrieval_tag, "prompt": PROMPT_ID, "seed": "42"},
            extra_metadata={
                "ontology_arm": arm, "ontology_modules_list": modules,
                "ontology_node_count": len(ontology.nodes), "ontology_rel_count": len(ontology.relationships),
                "nodes_created": nodes_created, "relationships_created": rels_created,
                "prompt_id": PROMPT_ID, "experiment_database": database,
                "case_query": case["query"], "case_n_refs": case["n_refs"], "case_type": case["type"],
            },
        )
        ans_err = ""
        t1 = time.perf_counter()

        def _work(c=context, _exp=case["expected_answer"]):
            ans = _grok_answer(llm, case["query"], c)
            # Attach the cheap deterministic metric as a feedback score so the
            # Opik UI shows a sortable/chartable column (judge_score is backfilled
            # offline by finder_judge). Set metadata too.
            m = evaluate_answer(_exp, ans)
            bc.set_opik_feedback_scores({
                "number_overlap": m["number_overlap_ratio"],
                "contains_match": 1.0 if m["contains_match"] else 0.0,
            })
            bc.set_opik_trace_metadata(name=tname, tags=tags, metadata=metadata)
            return ans

        try:
            if llm is None:
                raise RuntimeError("LLM backend unavailable")
            answer = bc.run_under_opik_track(name=tname, tags=tags, metadata=metadata, work_fn=_work)
        except Exception as exc:
            answer, ans_err = "", f"{type(exc).__name__}: {exc}"
        ask_ms = round((time.perf_counter() - t1) * 1000, 2)
        metrics = evaluate_answer(case["expected_answer"], answer)
        result = {
            "case_id": case["case_id"], "slice": case["slice"], "category": case["category"],
            "type": case["type"], "n_refs": case["n_refs"], "arm": arm, "mode": mode_name,
            "retrieval": retrieval_tag, "ontology_modules": modules, "ontology_hash": onto_hash,
            "ontology_node_count": len(ontology.nodes), "ontology_rel_count": len(ontology.relationships),
            "model": llm_spec, "prompt_id": PROMPT_ID, "prompt_hash": prompt_hash,
            "workspace_id": workspace_id, "graph_backend": "neo4j", "database": database,
            "query": case["query"], "expected_answer": case["expected_answer"], "answer": answer,
            "evaluation": metrics,
            "latency_ms": {"add": add_ms, "ask": ask_ms,
                           "total": round((time.perf_counter() - started) * 1000, 2)},
            "nodes_created": nodes_created, "relationships_created": rels_created,
            "graph_context_chars": len(graph_ctx), "vector_context_chars": len(vec_ctx),
            "error": error or ans_err,
        }
        try:
            bc.atomic_write_json(
                out_partial_dir / f"{case['slice']}_{case['case_id']}_{arm}_{mode_name}.json", result)
        except Exception as exc:
            print(f"  [warn] partial write failed: {exc}", flush=True)
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Aggregation: per (slice × arm)
# ---------------------------------------------------------------------------

def summarize(results: list[dict]) -> dict:
    by: dict[tuple[str, str, str], list[dict]] = {}
    for r in results:
        by.setdefault((r["slice"], r["arm"], r.get("mode", "graph")), []).append(r)
    out = {}
    for (slc, arm, mode), runs in sorted(by.items()):
        ov = [r["evaluation"]["number_overlap_ratio"] for r in runs]
        ct = [r["evaluation"]["contains_match"] for r in runs]
        nodes = [r["nodes_created"] for r in runs]
        errs = sum(1 for r in runs if r["error"])
        out[f"{slc}|{arm}|{mode}"] = {
            "slice": slc, "arm": arm, "mode": mode, "n": len(runs),
            "number_overlap_mean": round(sum(ov) / len(ov), 3) if ov else 0.0,
            "contains_rate": round(sum(ct) / len(ct), 3) if ct else 0.0,
            "nodes_mean": round(sum(nodes) / len(nodes), 1) if nodes else 0.0,
            "errors": errs,
        }
    return out


def print_table(summary: dict) -> None:
    print("\nslice                   | arm          | mode         |  n | overlap | contains | nodes | err")
    print("-" * 100)
    for row in summary.values():
        print(f"{row['slice']:<23} | {row['arm']:<12} | {row['mode']:<12} | {row['n']:2d} | "
              f"{row['number_overlap_mean']:.3f}   | {row['contains_rate']:.2f}     | "
              f"{row['nodes_mean']:5.1f} | {row['errors']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-slice", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--llm", default=os.environ.get("SEOCHO_LLM", "grok/grok-4.3"))
    ap.add_argument("--database", default=os.environ.get("SEOCHO_EXPERIMENT_DB", "yitae0530grok"),
                    help="Fixed experiment DB (Opik-style name+date+model, Neo4j-sanitized; no hyphens).")
    ap.add_argument("--arms", default="non-ontology,small,medium,large")
    ap.add_argument("--limit-cases", type=int, default=0, help="Cap total cases (smoke). 0=all.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run-prefix",
                    default=f"4arm-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    args = ap.parse_args()

    bc.bootstrap(verbose=True)
    bc.set_global_determinism(args.seed)

    meta = GROK_META_PROMPT.read_text(encoding="utf-8")
    idx = meta.find("## ROLE")
    system_tmpl = meta[idx:] if idx >= 0 else meta
    prompt_hash = bc.short_hash(system_tmpl)
    extraction_tmpl = KGPromptTemplate(
        system=system_tmpl,
        user="Source 10-K text to extract into the graph:\n\n{{text}}",
    )
    print(f"== extraction prompt: {GROK_META_PROMPT.name} ({len(system_tmpl)} chars, hash={prompt_hash}) ==")

    arms = [a.strip() for a in args.arms.split(",") if a.strip() in ARMS]
    cases = load_sample(args.n_per_slice, args.seed)
    if args.limit_cases:
        cases = cases[: args.limit_cases]
    total = len(cases) * len(arms)
    print(f"== plan: {len(cases)} cases × {len(arms)} arms = {total} runs ==")
    print(f"   arms: {arms}")
    by_slice: dict[str, int] = {}
    for c in cases:
        by_slice[c["slice"]] = by_slice.get(c["slice"], 0) + 1
    print(f"   sample per slice: {by_slice}")
    if args.dry_run:
        for c in cases:
            print(f"   {c['slice']:<23} {c['case_id']}  {c['query'][:70]}")
        print("(dry-run: stopping before LLM/graph work)")
        return 0

    # NOTE: we deliberately do NOT call configure_tracing_from_env() here.
    # Enabling SEOCHO's OpikBackend also (a) emits internal sdk.extraction/sdk.query
    # traces and (b) wraps the LLM client with opik track_openai (chat_completion_create
    # traces) — both flood the Opik project with logs users don't recognize. Our
    # experiment traces come ONLY from bc.run_under_opik_track (@track) + set_opik_
    # trace_metadata, which use opik's own env/config (~/.opik.config) directly.
    # Result: the Opik project shows exactly one clean, tagged trace per run.
    print(f"== tracing: experiment-traces-only (no SEOCHO backend) "
          f"project={os.environ.get('OPIK_PROJECT_NAME')} ws={os.environ.get('OPIK_WORKSPACE')} ==")

    def flush_tracing():  # opik @track flushes on its own; best-effort explicit flush
        try:
            import opik
            opik.flush_tracker()
        except Exception:
            pass

    # Validate + create the single experiment DB up front, wait until online.
    from seocho.store.graph import Neo4jGraphStore, sanitize_database_name
    database = sanitize_database_name(args.database)
    _gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                          os.environ.get("NEO4J_PASSWORD", ""))
    _ensure_db_ready(_gs, database)
    _gs.close()
    print(f"== experiment database: {database} (online) ==")

    from openai import OpenAI
    oai_client = OpenAI(timeout=60)   # for vector-context embeddings (same model as vector lane)

    out_dir = ROOT / "outputs" / "evaluation" / "finder_4arm_sample" / args.run_prefix
    out_partial = out_dir / "partial"
    out_partial.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    started = time.perf_counter()
    run_i = 0
    for case in cases:
        for arm in arms:
            run_i += 1
            print(f"\n>>> [{run_i}/{total}] {case['slice']} {case['case_id']} ({arm})")
            mode_results = run_one(case=case, arm=arm, modules=ARMS[arm], llm_spec=args.llm,
                                   extraction_tmpl=extraction_tmpl, prompt_hash=prompt_hash,
                                   run_prefix=args.run_prefix, database=database,
                                   oai_client=oai_client, out_partial_dir=out_partial)
            for res in mode_results:
                ev = res["evaluation"]
                mark = "OK" if not res["error"] else "ERR"
                print(f"    [{res['mode']:<12}] {mark}  overlap={ev['number_overlap_ratio']:.2f} "
                      f"nums={ev['shared_numbers']}/{ev['expected_number_count']} "
                      f"contains={ev['contains_match']} nodes={res['nodes_created']} "
                      f"ask={res['latency_ms']['ask']}ms")
                if res["error"]:
                    print(f"      error: {res['error']}")
                results.append(res)

    try:
        flush_tracing()
    except Exception:
        pass

    summary = summarize(results)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_prefix": args.run_prefix, "llm": args.llm, "seed": args.seed,
        "database": database,
        "n_per_slice": args.n_per_slice, "arms": arms,
        "prompt_id": PROMPT_ID, "prompt_hash": prompt_hash,
        "opik_project": os.environ.get("OPIK_PROJECT_NAME", ""),
        "opik_workspace": os.environ.get("OPIK_WORKSPACE", ""),
        "tracing_backends": current_backend_names(),
        "total_runs": len(results),
        "total_wall_seconds": round(time.perf_counter() - started, 2),
        "summary": summary,
        "results": results,
    }
    agg = out_dir / "aggregate.json"
    bc.atomic_write_json(agg, payload)
    print(f"\n== wrote {agg.relative_to(ROOT)} ==")
    print_table(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
