#!/usr/bin/env python3
"""E2E architecture suite — exercise the REAL structured query pipeline and
compare SEOCHO features ON vs OFF (before/after ablation), per generator.

Unlike finder_4arm_sample.py (graph-as-context dump), this drives the runtime
path: routing → text2cypher (deterministic templates + intent) → executor →
answer synthesis, on graphs already built by the per-model sweeps. With
SEOCHO_E2E_BASELINE=1 it restores pre-feature behavior (OP1 off: company–metric
edge hard-required; OP-routing off: no graph-context fallback) so we can measure
why the answers differ. Writes finder-partial-format JSON so finder_judge.py can
LLM-judge the answers. Read-only against the graphs.

Usage:
  # features ON (default):
  python scripts/benchmarks/e2e_probe.py --model MiniMax-M2.5 --db yitae0602minimaxm25 \
      --src-run sweep-mara-mm --out-run e2e-mm-features --limit 30
  # features OFF (baseline):
  SEOCHO_E2E_BASELINE=1 python scripts/benchmarks/e2e_probe.py ... --out-run e2e-mm-baseline
"""
from __future__ import annotations
import os, sys, json, glob, time, argparse, statistics, re
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v

from examples.finder.datasets.fibo_modules.compose import compose_modules
from seocho import Seocho
from seocho.store.graph import Neo4jGraphStore
from seocho.store.llm import create_llm_backend

ARM = "medium"
MODULES = ["be", "ind", "fbc", "dbt", "acc"]
_NUM = re.compile(r"-?\$?\d[\d,]*\.?\d*(?:%| million| billion| thousand)?", re.I)
_ORDERED_LIST_MARKER = re.compile(r"(?m)^\s*\d+\.\s+")
def nums(t):
    text = _ORDERED_LIST_MARKER.sub("", str(t or ""))
    return {n.replace(",", "").strip().lower() for n in _NUM.findall(text)}
def overlap(exp, act):
    e, a = nums(exp), nums(act)
    return len(e & a) / len(e) if e else 0.0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)            # MiniMax-M2.5 / gpt-oss-120b / DeepSeek-V3.1
    ap.add_argument("--db", required=True)                # yitae0602minimaxm25 ...
    ap.add_argument("--src-run", required=True)           # sweep-mara-mm (source of cases/gold/baseline)
    ap.add_argument("--out-run", required=True)           # e2e-mm-features
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--case-retries", type=int, default=int(os.environ.get("SEOCHO_E2E_CASE_RETRIES", "1")))
    ap.add_argument("--llm-timeout", type=float, default=float(os.environ.get("SEOCHO_E2E_LLM_TIMEOUT", "45")))
    ap.add_argument("--llm-timeout-retries", type=int, default=int(os.environ.get("SEOCHO_E2E_LLM_TIMEOUT_RETRIES", "1")))
    ap.add_argument("--resume", action="store_true", help="Skip cases whose partial JSON already exists.")
    args = ap.parse_args()

    baseline = str(os.environ.get("SEOCHO_E2E_BASELINE", "")).strip() not in ("", "0", "false")
    cases = []
    for f in sorted(glob.glob(f"{ROOT}/outputs/evaluation/finder_4arm_sample/{args.src_run}/partial/*_{ARM}_graph.json")):
        if "vector_graph" in f:
            continue
        cases.append(json.load(open(f)))
        if len(cases) >= args.limit:
            break

    onto = compose_modules(MODULES)
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    llm = create_llm_backend(provider="mara", model=args.model, timeout=args.llm_timeout)
    if hasattr(llm, "_TIMEOUT_RETRIES"):
        llm._TIMEOUT_RETRIES = max(0, args.llm_timeout_retries)
    out_dir = ROOT / "outputs" / "evaluation" / "e2e_probe" / args.out_run / "partial"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"== E2E suite: model={args.model} db={args.db} cases={len(cases)} "
          f"SEOCHO_E2E_BASELINE={'ON(features OFF)' if baseline else 'off(features ON)'} ==\n")

    summary = []
    for c in cases:
        ws = c["workspace_id"]
        out_path = out_dir / f"{c['slice']}_{c['case_id']}_e2e.json"
        if args.resume and out_path.exists():
            rec = json.loads(out_path.read_text())
            summary.append({"slice": rec["slice"], "ov": rec["evaluation"]["number_overlap_ratio"], "gac": rec.get("gac_overlap", 0.0),
                            "support": rec.get("support"), "supported": rec.get("supported"), "latency": rec.get("latency_breakdown_ms", {}),
                            "answer_source": rec.get("answer_source"), "wall_s": rec.get("wall_s", 0.0),
                            "evidence_swarm": rec.get("evidence_swarm", {}), "support_quality_gap": rec.get("support_quality_gap", "")})
            print(f"[{c['case_id']}] {c['slice']:<22} SKIP existing partial")
            continue
        client = Seocho(ontology=onto, graph_store=gs, llm=llm, workspace_id=ws)
        client.default_database = args.db
        t0 = time.perf_counter()
        try:
            resp = _ask_with_case_retries(
                client,
                c["query"],
                database=args.db,
                case_retries=max(0, args.case_retries),
            )
            ans = resp.response
            env = resp.answer_envelope or {}
            support = env.get("support_assessment") or {}
            evidence = resp.evidence.to_dict()
            evidence_swarm = evidence.get("evidence_swarm") or {}
            # bottleneck instrumentation: per-stage latency + which lane answered
            latency = env.get("latency_breakdown_ms") or {}
            answer_source = env.get("answer_source") or (env.get("agent_pattern") or {}).get("answer_source") or ""
            agent_pat = env.get("agent_pattern") or {}
            ov = overlap(c["expected_answer"], ans)
            dt = round(time.perf_counter() - t0, 1)
        except Exception as e:
            ans, ov, support, dt = f"ERROR: {type(e).__name__}: {str(e)[:120]}", 0.0, {}, 0.0
            latency, answer_source, agent_pat = {}, "error", {}
            evidence, evidence_swarm = {}, {}
        rec = dict(c)  # inherit gold/query/slice/arm/etc for finder_judge compatibility
        rec["answer"] = ans
        rec["retrieval"] = "structured"
        rec["mode"] = "structured"
        rec["evaluation"] = {"number_overlap_ratio": ov,
                             "shared_numbers": len(nums(c["expected_answer"]) & nums(ans)),
                             "expected_number_count": len(nums(c["expected_answer"])),
                             "contains_match": False}
        rec["e2e_baseline"] = baseline
        rec["e2e_model"] = args.model
        rec["support"] = support.get("status") or support.get("supported")
        rec["supported"] = bool(support.get("supported")) or str(rec["support"]).lower() in {"supported", "derived_supported", "true"}
        rec["support_assessment"] = support
        rec["support_quality_gap"] = _support_quality_gap(
            expected=c["expected_answer"],
            answer=ans,
            support=rec["support"],
            supported=rec["supported"],
            overlap_ratio=ov,
            answer_source=answer_source,
        )
        rec["evidence_bundle"] = evidence
        rec["evidence_swarm"] = evidence_swarm
        rec["gac_overlap"] = c["evaluation"]["number_overlap_ratio"]
        rec["latency_breakdown_ms"] = latency
        rec["answer_source"] = answer_source
        rec["reasoning_attempts"] = agent_pat.get("reasoning_attempts")
        rec["wall_s"] = dt
        out_path.write_text(json.dumps(rec, default=str, indent=2))
        summary.append({"slice": c["slice"], "ov": ov, "gac": c["evaluation"]["number_overlap_ratio"],
                        "support": rec["support"], "supported": rec["supported"], "latency": latency, "answer_source": answer_source,
                        "wall_s": dt, "evidence_swarm": evidence_swarm, "support_quality_gap": rec["support_quality_gap"]})
        print(f"[{c['case_id']}] {c['slice']:<22} struct={ov:.2f} gac={rec['gac_overlap']:.2f} "
              f"support={rec['support']} gap={rec['support_quality_gap']} ({dt}s)")

    gs.close()
    if summary:
        ms = statistics.mean(x["ov"] for x in summary)
        mg = statistics.mean(x["gac"] for x in summary)
        sup = sum(1 for x in summary if bool(x.get("supported")) or str(x["support"]).lower() in ("supported", "derived_supported", "true"))
        print("\n" + "=" * 64)
        print(f"{args.out_run}: structured(number_overlap)={ms:.3f} | graph-as-context={mg:.3f} | supported {sup}/{len(summary)}")
        bys = defaultdict(list)
        for x in summary:
            bys[x["slice"]].append(x)
        for s in sorted(bys):
            v = bys[s]
            print(f"  {s:<24} struct={statistics.mean(x['ov'] for x in v):.2f}  gac={statistics.mean(x['gac'] for x in v):.2f}")

        # --- E2E BOTTLENECK ANALYSIS ---
        print("\n--- bottleneck: mean latency per pipeline stage (ms) ---")
        stage_tot = defaultdict(float); stage_n = defaultdict(int)
        for x in summary:
            for k, val in (x.get("latency") or {}).items():
                try:
                    stage_tot[k] += float(val); stage_n[k] += 1
                except Exception:
                    pass
        for k in sorted(stage_tot, key=lambda k: -stage_tot[k] / max(1, stage_n[k])):
            print(f"  {k:<28} {stage_tot[k] / max(1, stage_n[k]):8.1f} ms   (n={stage_n[k]})")
        print("\n--- bottleneck: answer_source distribution (which lane answered) ---")
        srcs = defaultdict(int)
        for x in summary:
            srcs[x.get("answer_source") or "?"] += 1
        for k, n in sorted(srcs.items(), key=lambda kv: -kv[1]):
            print(f"  {k:<28} {n}/{len(summary)}")
        print("\n--- evidence swarm: hardness and critical path ---")
        hardness = defaultdict(int)
        critical = defaultdict(int)
        next_steps = defaultdict(int)
        enabled = 0
        for x in summary:
            swarm = x.get("evidence_swarm") or {}
            if swarm.get("enabled"):
                enabled += 1
            hardness[str(swarm.get("hardness") or "?")] += 1
            next_steps[str(swarm.get("recommended_next_step") or "?")] += 1
            for scout_id in swarm.get("critical_path") or []:
                critical[str(scout_id)] += 1
        print(f"  enabled                     {enabled}/{len(summary)}")
        for k, n in sorted(hardness.items(), key=lambda kv: -kv[1]):
            print(f"  hardness:{k:<19} {n}/{len(summary)}")
        for k, n in sorted(critical.items(), key=lambda kv: -kv[1]):
            print(f"  critical:{k:<19} {n}/{len(summary)}")
        for k, n in sorted(next_steps.items(), key=lambda kv: -kv[1]):
            print(f"  next:{k:<23} {n}/{len(summary)}")
        print(f"  mean wall: {statistics.mean(x['wall_s'] for x in summary):.1f}s/case")
        print("\n--- support quality gap (support vs deterministic answer metric) ---")
        gaps = defaultdict(int)
        for x in summary:
            gaps[str(x.get("support_quality_gap") or "none")] += 1
        for k, n in sorted(gaps.items(), key=lambda kv: -kv[1]):
            print(f"  {k:<32} {n}/{len(summary)}")
        print(f"\nwrote {len(summary)} partials -> {out_dir}")

def _ask_with_case_retries(client, query, *, database: str, case_retries: int):
    attempts = 0
    while True:
        try:
            return client.ask_response(query, database=database, query_mode="semantic", repair_budget=2)
        except Exception as exc:
            if attempts >= case_retries or not _is_transient_llm_error(exc):
                raise
            attempts += 1
            delay = min(8.0, 2.0 * attempts)
            print(f"    transient case error ({type(exc).__name__}); retry {attempts}/{case_retries} after {delay:.1f}s")
            time.sleep(delay)


def _is_transient_llm_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return any(token in name or token in text for token in ("timeout", "rate limit", "connection", "temporar", "gateway"))


def _support_quality_gap(*, expected: str, answer: str, support, supported: bool, overlap_ratio: float, answer_source: str) -> str:
    expected_nums = nums(expected)
    if str(answer_source or "") == "error" or str(answer or "").startswith("ERROR:"):
        return "answer_error"
    if not expected_nums:
        return "no_numeric_gold"
    if supported and overlap_ratio <= 0.0:
        return "supported_zero_number_overlap"
    if supported and overlap_ratio < 0.5:
        return "supported_low_number_overlap"
    if not supported and overlap_ratio >= 0.5:
        return "unsupported_but_numeric_match"
    return "aligned"

if __name__ == "__main__":
    main()
