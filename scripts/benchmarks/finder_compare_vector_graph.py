#!/usr/bin/env python3
"""Vector vs Graph vs Hybrid retrieval comparison on FinDER (multi-LLM, Tier 3).

For each (LLM × case × mode) combination:
  - retrieve context three ways (vector / graph / hybrid)
  - generate answer with the LLM (provider/model from --llms list)
  - evaluate with token_f1 + LLM-judge (judge_spec, response_format=json_object + retry)
  - emit Opik traces tagged with the 4 core meta dimensions (dataset / model /
    flow / ontology) via ``bench_common.build_core_meta``

Modes of operation:
  --smoke               : 1 case × N llms × 3 modes (default 1 case from CLI or 4af93b03)
  --case CASE_ID        : single case (overrides --phases)
  --phases P0,P1A,...   : run specified phase codes (uses bench_common.PHASES)
  --stratified FRAC     : stratified sample FRAC of all_slices.csv (per slice)
                          NOTE: graph/hybrid modes require pre-extracted lbug
                          per case; missing graphs degrade to empty context.
  --llms kimi,openai,xai,deepseek
  --judge openai/gpt-4o-mini   (default; gpt-4o-mini supports response_format=json_object)

Outputs:
  outputs/evaluation/finder_compare/<run_prefix>/partial/{llm}__{phase}_{case}_{mode}.json
  outputs/evaluation/finder_compare/<run_prefix>/aggregate.json
"""

from __future__ import annotations

import argparse
import math
import os
import re
import string
import sys
import time
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for path in (SRC, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from examples.finder.lib import bench_common as bc  # noqa: E402
from examples.finder.lib import llm_io  # noqa: E402

DATASET_NAME = "all_slices.csv"
DEFAULT_SMOKE_CASE = "4af93b03"  # P0 ROST


# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------


def load_case_data(case_id: str) -> dict:
    import pandas as pd

    df = pd.read_csv(ROOT / ".seocho/datasets/finder/slices/all_slices.csv")
    row = df[df["_id"] == case_id]
    if row.empty:
        raise KeyError(f"case {case_id} not found in slices CSV")
    r = row.iloc[0]
    return {
        "case_id": r["_id"],
        "slice": r["slice"],
        "category": r["category"],
        "query": r["query"],
        "expected_answer": r["answer"],
    }


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------


def vector_retrieve(query: str, *, case_id: str, top_k: int = 5) -> str:
    """LanceDB semantic top-k over the phase evidence index."""
    import lancedb
    from openai import OpenAI

    client = OpenAI(timeout=60)
    qvec = (
        client.embeddings.create(model="text-embedding-3-small", input=[query])
        .data[0]
        .embedding
    )

    db = lancedb.connect(str(ROOT / ".seocho/lancedb"))
    table = db.open_table("finder_phase_evidence")
    hits = table.search(qvec).limit(top_k).to_list()

    pieces = []
    for i, h in enumerate(hits, 1):
        header = f"[#{i} phase={h['phase']} case={h['case_id']} idx={h.get('ref_idx',0)} d={h.get('_distance',0):.3f}]"
        pieces.append(f"{header}\n{h['text']}")
    return "\n\n---\n\n".join(pieces)


_KEEP_PROPS = frozenset(
    {
        "name",
        "value",
        "period",
        "amount",
        "amount_per_share",
        "principal_amount",
        "coupon_rate",
        "maturity_date",
        "description",
        "ticker",
        "jurisdiction",
        "category",
        "standard",
    }
)


def graph_retrieve(case_id: str, query: str) -> str:
    """Neo4j retrieve: case-scoped entities + 1-hop relations rendered as text."""
    from neo4j import GraphDatabase

    drv = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )

    try:
        with drv.session() as s:
            nodes_rows = s.run(
                """
                MATCH (n {_case_id: $cid})
                WHERE NOT n:Chunk AND NOT n:Document AND NOT n:DocumentVersion AND NOT n:Section
                RETURN labels(n)[0] AS lbl, properties(n) AS props
                """,
                cid=case_id,
            ).data()
            edge_rows = s.run(
                """
                MATCH (a {_case_id: $cid})-[r]->(b {_case_id: $cid})
                WHERE NOT a:Chunk AND NOT a:Document AND NOT a:DocumentVersion AND NOT a:Section
                  AND NOT b:Chunk AND NOT b:Document AND NOT b:DocumentVersion AND NOT b:Section
                RETURN labels(a)[0] AS a_lbl, a.name AS a_name,
                       type(r) AS rt,
                       labels(b)[0] AS b_lbl, b.name AS b_name
                """,
                cid=case_id,
            ).data()
            chunk_rows = s.run(
                """
                MATCH (c:Chunk {_case_id: $cid})
                RETURN c.content_preview AS preview, c.content AS content
                LIMIT 3
                """,
                cid=case_id,
            ).data()
    finally:
        drv.close()

    lines: list[str] = ["=== Graph entities ==="]
    for n in nodes_rows:
        lbl = n["lbl"] or "Entity"
        props = n["props"] or {}
        clean = {
            k: v
            for k, v in props.items()
            if k in _KEEP_PROPS and v is not None and v != ""
        }
        lines.append(f"({lbl}) " + ", ".join(f"{k}={v}" for k, v in clean.items()))
    if edge_rows:
        lines.append("\n=== Relations ===")
        for e in edge_rows:
            lines.append(
                f"({e['a_lbl']} {e['a_name']}) -[{e['rt']}]-> ({e['b_lbl']} {e['b_name']})"
            )
    if chunk_rows:
        lines.append("\n=== Evidence chunks (from graph) ===")
        for i, c in enumerate(chunk_rows, 1):
            text = (c.get("content") or c.get("preview") or "").strip()
            if text:
                lines.append(f"[chunk #{i}] {text[:1200]}")
    return "\n".join(lines)


def hybrid_retrieve(query: str, *, case_id: str, top_k: int = 5) -> str:
    v = vector_retrieve(query, case_id=case_id, top_k=top_k)
    g = graph_retrieve(case_id, query)
    return f"===== VECTOR CONTEXT =====\n{v}\n\n===== GRAPH CONTEXT =====\n{g}"


# ---------------------------------------------------------------------------
# generation (Kimi via llm_io)
# ---------------------------------------------------------------------------

_BASE_SYSTEM = (
    "You are answering a financial question using the provided context. "
    "Ground every claim in the context. If the context lacks the needed "
    "information, say so explicitly. Show calculation steps for any "
    "arithmetic, preserve units and periods."
)


def generate_answer(
    query: str, context: str, *, llm_spec, meta_prompt: str, client
) -> str:
    system_text = bc.compose_system_prompt(meta_prompt, _BASE_SYSTEM)
    user_text = f"## Context\n{context}\n\n## Question\n{query}\n\n## Answer\n"
    return llm_io.chat_complete(
        client=client,
        model=llm_spec.model,
        system=system_text,
        user=user_text,
        temperature=(
            llm_spec.forced_temperature
            if llm_spec.forced_temperature is not None
            else 0.0
        ),
        label=f"answer/{llm_spec.provider}",
        max_attempts=3,
        spec=llm_spec,
    )


# ---------------------------------------------------------------------------
# evaluation: token_f1
# ---------------------------------------------------------------------------


def _normalize_for_f1(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    s = s.translate(str.maketrans("", "", string.punctuation))
    s = re.sub(r"\s+", " ", s).strip()
    return s


def token_f1(prediction: str, gold: str) -> float:
    pred_toks = _normalize_for_f1(prediction).split()
    gold_toks = _normalize_for_f1(gold).split()
    if not pred_toks or not gold_toks:
        return 0.0
    common = Counter(pred_toks) & Counter(gold_toks)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    p = num_same / len(pred_toks)
    r = num_same / len(gold_toks)
    return round(2 * p * r / (p + r), 4)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


@dataclass
class RunRecord:
    phase: str
    case_id: str
    slice_tag: str
    category: str
    mode: str  # 'vector' / 'graph' / 'hybrid'
    provider: str
    llm: str
    judge: str
    query: str
    gold: str
    context_chars: int
    answer: str
    answer_chars: int
    token_f1: float
    judge_score: int
    judge_rationale: str
    latency_s: float
    graph_quality: str = "raw"
    cypher_agent_version: str = "v1"
    error: str = ""


def run_one(
    *,
    phase: str,
    case_id: str,
    modules_label: str,
    mode: str,
    case: dict,
    meta_prompt: str,
    run_prefix: str,
    llm_spec: llm_io.LLMSpec,
    chat_client,
    judge_spec: llm_io.LLMSpec,
    judge_client,
    out_partial_dir: Path,
    graph_quality: str = "raw",
    cypher_agent_version: str = "v1",
    retrieval_k: int = 5,
) -> RunRecord:
    name = f"compare/{llm_spec.provider}/{mode}/{phase}/{case_id}"
    onto_hash = bc.short_hash(modules_label)
    prompt_hash = bc.short_hash(meta_prompt) if meta_prompt else "noprompt"

    tags, metadata = bc.build_core_meta(
        dataset_name=DATASET_NAME,
        dataset_index=f"{case['slice']}/{case_id}",
        case_id=case_id,
        slice_tag=case["slice"],
        category=case["category"],
        llm_spec=llm_spec.llm_string,
        provider=llm_spec.provider,
        judge_spec=judge_spec.llm_string,
        mode=mode,
        retrieval_k=retrieval_k,
        reasoning_mode=False,
        repair_budget=0,
        flow="graphrag",
        graph_quality=graph_quality,
        cypher_agent_version=cypher_agent_version,
        ontology_hash=onto_hash,
        ontology_modules=modules_label,
        prompt_hash=prompt_hash,
        run_prefix=run_prefix,
        extra_metadata={"phase": phase, "query": case["query"]},
    )

    started = time.perf_counter()
    answer = ""
    context = ""
    error = ""

    def _work():
        nonlocal context, answer
        if mode == "vector":
            context = vector_retrieve(case["query"], case_id=case_id, top_k=retrieval_k)
        elif mode == "graph":
            context = graph_retrieve(case_id, case["query"])
        elif mode == "hybrid":
            context = hybrid_retrieve(case["query"], case_id=case_id, top_k=retrieval_k)
        else:
            raise ValueError(f"unknown mode {mode}")
        answer = generate_answer(
            case["query"],
            context,
            llm_spec=llm_spec,
            meta_prompt=meta_prompt,
            client=chat_client,
        )
        return answer

    try:
        bc.run_under_opik_track(name=name, tags=tags, metadata=metadata, work_fn=_work)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    latency = round(time.perf_counter() - started, 2)
    f1 = token_f1(answer, case["expected_answer"]) if answer else 0.0

    judge_name = f"judge/{llm_spec.provider}/{mode}/{phase}/{case_id}"
    judge_tags = tags + ["role:judge"]
    judge: dict = {"score": -1, "rationale": "(skipped)"}
    if answer:
        try:
            judge = bc.run_under_opik_track(
                name=judge_name,
                tags=judge_tags,
                metadata=metadata,
                work_fn=lambda: llm_io.llm_judge(
                    client=judge_client,
                    model=judge_spec.model,
                    question=case["query"],
                    gold=case["expected_answer"],
                    prediction=answer,
                    spec=judge_spec,
                ),
            )
        except Exception as exc:
            judge = {
                "score": -1,
                "rationale": f"judge call err: {type(exc).__name__}: {exc}",
            }

    rec = RunRecord(
        phase=phase,
        case_id=case_id,
        slice_tag=case["slice"],
        category=case["category"],
        mode=mode,
        provider=llm_spec.provider,
        llm=llm_spec.llm_string,
        judge=judge_spec.llm_string,
        query=case["query"],
        gold=case["expected_answer"],
        context_chars=len(context),
        answer=answer,
        answer_chars=len(answer),
        token_f1=f1,
        judge_score=int(judge.get("score", -1) or -1),
        judge_rationale=str(judge.get("rationale", "")),
        latency_s=latency,
        graph_quality=graph_quality,
        cypher_agent_version=cypher_agent_version,
        error=error,
    )

    # Partial checkpoint — atomic (includes provider in filename for multi-LLM)
    partial_path = (
        out_partial_dir / f"{llm_spec.provider}__{phase}_{case_id}_{mode}.json"
    )
    try:
        bc.atomic_write_json(partial_path, asdict(rec))
    except Exception as exc:
        print(f"  [warn] partial write failed: {exc}", flush=True)

    return rec


def _build_case_list(args, slices_csv_path: Path) -> list[tuple[str, dict]]:
    """Resolve (phase_code, case_dict) list from CLI args.

    Priority: --case > --stratified > --phases > all PHASES (full 9 cases).
    Smoke mode (--smoke) forces a single case (default DEFAULT_SMOKE_CASE).
    """
    import pandas as pd

    df = pd.read_csv(slices_csv_path)

    # Resolve case_id → phase from bench_common.PHASES (single source of truth)
    case_to_phase = {c.case_id: p.code for p in bc.PHASES for c in p.cases}

    if args.smoke:
        cid = args.case or DEFAULT_SMOKE_CASE
        row = df[df["_id"] == cid]
        if row.empty:
            raise SystemExit(f"smoke case {cid!r} not in slices CSV")
        return [(case_to_phase.get(cid, "smoke"), _row_to_case(row.iloc[0]))]

    if args.case:
        row = df[df["_id"] == args.case]
        if row.empty:
            raise SystemExit(f"case {args.case!r} not in slices CSV")
        return [(case_to_phase.get(args.case, "ad-hoc"), _row_to_case(row.iloc[0]))]

    if args.stratified:
        fraction = float(args.stratified)
        sampled = bc.stratified_sample(df, fraction=fraction, seed=42)
        return [
            (case_to_phase.get(r["_id"], r["slice"]), _row_to_case(r))
            for _, r in sampled.iterrows()
        ]

    phase_codes: list[str] = []
    if args.phases == "all":
        phase_codes = [p.code for p in bc.PHASES]
    else:
        wanted = {p.strip().upper() for p in args.phases.split(",") if p.strip()}
        phase_codes = [p.code for p in bc.PHASES if p.code in wanted]

    out = []
    for p in bc.PHASES:
        if phase_codes and p.code not in phase_codes:
            continue
        for cspec in p.cases:
            row = df[df["_id"] == cspec.case_id]
            if row.empty:
                print(
                    f"  ! case {cspec.case_id} missing in slices CSV — skipping",
                    flush=True,
                )
                continue
            out.append((p.code, _row_to_case(row.iloc[0])))
    return out


def _row_to_case(r) -> dict:
    return {
        "case_id": r["_id"],
        "slice": r["slice"],
        "category": r["category"],
        "query": r["query"],
        "expected_answer": r["answer"],
    }


def _modules_label_for_phase(phase_code: str) -> str:
    try:
        return "+".join(bc.get_phase(phase_code).treatment_modules)
    except KeyError:
        return "baseline"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--llms",
        default="kimi,openai,xai,deepseek",
        help="Comma list of provider names (default: all 4).",
    )
    parser.add_argument(
        "--judge",
        default="openai/gpt-4o-mini",
        help="Judge LLM spec (provider/model). Default gpt-4o-mini.",
    )
    parser.add_argument(
        "--modes", default="vector,graph,hybrid", help="Comma list of retrieval modes."
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Smoke mode: 1 case × all llms × all modes (default case: 4af93b03).",
    )
    parser.add_argument(
        "--case", default="", help="Single case_id (overrides --phases/--stratified)."
    )
    parser.add_argument(
        "--phases",
        default="all",
        help="Comma list of phase codes (P0,P1A,...) or 'all'.",
    )
    parser.add_argument(
        "--stratified",
        default="",
        help="Stratified sample fraction (e.g. 0.10 for 10%% per slice).",
    )
    parser.add_argument("--retrieval-k", type=int, default=5)
    parser.add_argument(
        "--graph-quality",
        default="raw",
        help="Meta dim for graph quality (raw/qualified/denormalized).",
    )
    parser.add_argument(
        "--cypher-agent", default="v1", help="Meta dim for text2cypher agent version."
    )
    parser.add_argument("--run-prefix", default="")
    args = parser.parse_args()

    bc.bootstrap(verbose=True)

    requested_providers = [p.strip().lower() for p in args.llms.split(",") if p.strip()]
    requested_modes = [m.strip().lower() for m in args.modes.split(",") if m.strip()]
    if not requested_providers or not requested_modes:
        raise SystemExit("--llms and --modes must each have at least one entry")
    unknown = [p for p in requested_providers if p not in llm_io.known_providers()]
    if unknown:
        raise SystemExit(
            f"unknown providers: {unknown}. Known: {llm_io.known_providers()}"
        )

    report = bc.preflight(
        strict=True,
        require_moonshot="kimi" in requested_providers,
        require_openai_chat=("openai" in requested_providers)
        or args.judge.startswith("openai/"),
        require_xai="xai" in requested_providers,
        require_deepseek="deepseek" in requested_providers,
        require_openai_embed=True,  # vector mode needs embeddings
        require_neo4j=any(m in requested_modes for m in ("graph", "hybrid")),
        require_slices=True,
    )
    report.print_table()
    if not report.ok:
        raise SystemExit("preflight failed — fix env/connectivity before running")

    try:
        from seocho.tracing import (
            configure_tracing_from_env,
            current_backend_names,
            flush_tracing,
        )

        configure_tracing_from_env()
        print(f"tracing backends: {current_backend_names()}")
    except Exception as e:
        print(f"tracing init skipped: {e}")

        def flush_tracing():
            pass  # type: ignore

    # Build LLM client pool
    answer_specs: dict[str, llm_io.LLMSpec] = {}
    answer_clients: dict[str, object] = {}
    for prov in requested_providers:
        spec = llm_io.parse_llm_spec(f"{prov}/")
        answer_specs[prov] = spec
        answer_clients[prov] = llm_io.make_chat_client(spec)

    judge_spec = llm_io.parse_llm_spec(args.judge)
    judge_client = llm_io.make_chat_client(judge_spec)

    meta_prompt = bc.load_meta_prompt()
    print(
        f"meta prompt loaded ({len(meta_prompt)} chars, hash={bc.short_hash(meta_prompt)})",
        flush=True,
    )

    cases = _build_case_list(
        args, ROOT / ".seocho/datasets/finder/slices/all_slices.csv"
    )
    if not cases:
        raise SystemExit("No cases resolved from CLI args")

    run_prefix = args.run_prefix or (
        ("smoke-" if args.smoke else "compare-")
        + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    )
    out_dir = ROOT / "outputs/evaluation/finder_compare" / run_prefix
    out_partial_dir = out_dir / "partial"
    out_partial_dir.mkdir(parents=True, exist_ok=True)

    records: list[RunRecord] = []
    total = len(cases) * len(requested_providers) * len(requested_modes)
    print(
        f"\n== plan: {len(cases)} cases × {len(requested_providers)} llms × {len(requested_modes)} modes = {total} runs ==",
        flush=True,
    )
    print(f"  providers: {requested_providers}")
    print(f"  modes:     {requested_modes}")
    print(f"  judge:     {judge_spec.llm_string}")
    print(f"  run_prefix:{run_prefix}")
    counter = 0
    started_all = time.perf_counter()

    for phase_code, case in cases:
        modules_label = _modules_label_for_phase(phase_code)
        for prov in requested_providers:
            spec = answer_specs[prov]
            client = answer_clients[prov]
            for mode in requested_modes:
                counter += 1
                label = f"[{counter:3d}/{total}] {prov:9s} {mode:6s} {phase_code}/{case['case_id']}"
                print(f">>> {label}", flush=True)
                rec = run_one(
                    phase=phase_code,
                    case_id=case["case_id"],
                    modules_label=modules_label,
                    mode=mode,
                    case=case,
                    meta_prompt=meta_prompt,
                    run_prefix=run_prefix,
                    llm_spec=spec,
                    chat_client=client,
                    judge_spec=judge_spec,
                    judge_client=judge_client,
                    out_partial_dir=out_partial_dir,
                    graph_quality=args.graph_quality,
                    cypher_agent_version=args.cypher_agent,
                    retrieval_k=args.retrieval_k,
                )
                mark = "OK" if not rec.error else "ERR"
                print(
                    f"    {mark}  f1={rec.token_f1:.3f}  judge={rec.judge_score:>3d}  "
                    f"latency={rec.latency_s}s  ctx={rec.context_chars}c  ans={rec.answer_chars}c",
                    flush=True,
                )
                if rec.error:
                    print(f"    error: {rec.error}", flush=True)
                records.append(rec)

    try:
        flush_tracing()
    except Exception:
        pass

    total_wall = round(time.perf_counter() - started_all, 2)

    # Aggregate (llm × mode)
    def avg(xs):
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    aggregate_by_llm_mode: dict[str, dict] = {}
    for r in records:
        key = f"{r.provider}/{r.mode}"
        aggregate_by_llm_mode.setdefault(key, []).append(r)

    aggregate_table: dict[str, dict] = {}
    for key, runs in aggregate_by_llm_mode.items():
        scores = [r.judge_score for r in runs if r.judge_score >= 0]
        aggregate_table[key] = {
            "n_runs": len(runs),
            "mean_token_f1": avg([r.token_f1 for r in runs]),
            "mean_judge_score": avg(scores),
            "judge_parse_err": sum(1 for r in runs if r.judge_score < 0 and r.answer),
            "judge_score_dist": dict(Counter(scores)),
            "mean_latency_s": avg([r.latency_s for r in runs]),
            "mean_context_chars": avg([r.context_chars for r in runs]),
        }

    # Legacy aggregate (by mode only — preserves backwards compat for v3 baseline diff)
    by_mode: dict[str, list[RunRecord]] = {m: [] for m in requested_modes}
    for r in records:
        by_mode.setdefault(r.mode, []).append(r)
    aggregate = {}
    for mode, runs in by_mode.items():
        scores = [r.judge_score for r in runs if r.judge_score >= 0]
        aggregate[mode] = {
            "n_runs": len(runs),
            "mean_token_f1": avg([r.token_f1 for r in runs]),
            "mean_judge_score": avg(scores),
            "judge_parse_err": sum(1 for r in runs if r.judge_score < 0 and r.answer),
            "judge_score_dist": dict(Counter(scores)),
            "mean_latency_s": avg([r.latency_s for r in runs]),
            "mean_context_chars": avg([r.context_chars for r in runs]),
        }

    case_winners: list[dict] = []
    case_ids_seen = {r.case_id for r in records}
    for case_id in sorted(case_ids_seen):
        cr = [r for r in records if r.case_id == case_id]
        best_f1 = max(cr, key=lambda r: r.token_f1)
        best_judge = max(cr, key=lambda r: r.judge_score)
        case_winners.append(
            {
                "case_id": case_id,
                "phase": cr[0].phase,
                "best_f1_combo": f"{best_f1.provider}/{best_f1.mode}",
                "best_f1": best_f1.token_f1,
                "best_judge_combo": f"{best_judge.provider}/{best_judge.mode}",
                "best_judge": best_judge.judge_score,
            }
        )

    aggregate_path = out_dir / "aggregate.json"
    bc.atomic_write_json(
        aggregate_path,
        {
            "run_prefix": run_prefix,
            "providers": requested_providers,
            "modes": requested_modes,
            "judge": judge_spec.llm_string,
            "dataset": DATASET_NAME,
            "prompt_hash": bc.short_hash(meta_prompt) if meta_prompt else "noprompt",
            "graph_quality": args.graph_quality,
            "cypher_agent_version": args.cypher_agent,
            "total_wall_s": total_wall,
            "n_cases": len(cases),
            "aggregate_by_mode": aggregate,
            "aggregate_by_llm_mode": aggregate_table,
            "case_winners": case_winners,
            "records": [asdict(r) for r in records],
        },
    )
    print(f"\nwrote {aggregate_path.relative_to(ROOT)}")

    print("\n===== aggregate by LLM × mode =====")
    print(
        f"{'llm/mode':28s} {'token_f1':>10s} {'judge':>8s} {'judge_err':>10s} {'latency_s':>10s} {'ctx_chars':>10s}"
    )
    for key in sorted(aggregate_table):
        ag = aggregate_table[key]
        print(
            f"{key:28s} {ag['mean_token_f1']:>10.4f} {ag['mean_judge_score']:>8.2f} "
            f"{ag['judge_parse_err']:>10d} {ag['mean_latency_s']:>10.2f} {ag['mean_context_chars']:>10.0f}"
        )

    print("\n===== per case winners =====")
    for w in case_winners:
        print(
            f"  {w['phase']}/{w['case_id']:8s}  "
            f"f1: {w['best_f1_combo']:24s}({w['best_f1']:.3f})  "
            f"judge: {w['best_judge_combo']:24s}({w['best_judge']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
