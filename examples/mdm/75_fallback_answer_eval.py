#!/usr/bin/env python3
"""Paid run: does the capability fallback improve ANSWERS, not just retrieval?

The zero-cost replay (log2026-capability-fallback-v1) showed the fallback raises
slot token recall from .045 to .152. That is a retrieval result. This script
closes the stated gap by answering the same 13 cases from the fallback evidence
with the same three models, so the answer-level claim is measured rather than
extrapolated.

Fairness (CLAUDE.md 20.3): the prompt, schema, scoring functions, tokenizer,
evidence serialization, temperature, and token cap are imported verbatim from
``64_revised_answer_eval.py``. The only thing that changes is which evidence arm
is served. Retrieval is not re-run -- the fallback evidence is assembled from the
already-frozen per-case arm rows -- so the comparison shares provenance exactly.

Fallback policy (fixed before this run, identical to the replay):
    router covered every required view -> serve its routed evidence
    otherwise                          -> serve the frozen TF-IDF top-2 evidence

Cost: 13 cases x 3 models = 39 completions, temperature 0, max 750 output tokens.
Resume-safe: every completion is persisted immediately and re-runs skip finished
work, so an interrupted run never repeats a paid call.
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
FALLBACK_DIR = ROOT / "outputs/evaluation/mdm_fedcat/log2026-capability-fallback-v1"
OUT = FALLBACK_DIR / "fallback_answers.json"

ARM = "sdcr_with_capability_fallback"
MODELS = ("DeepSeek-V3.1", "gpt-oss-120b", "MiniMax-M2.7")
BOOTSTRAP = 10_000
SEED = 42


def _load_reference():
    """Import the frozen answer harness so scoring cannot drift."""
    spec = importlib.util.spec_from_file_location(
        "revised_answer_eval64", ROOT / "examples/mdm/64_revised_answer_eval.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REF = _load_reference()


def clustered_bootstrap(pairs, iterations=BOOTSTRAP, seed=SEED):
    clusters: dict[str, list[float]] = {}
    for issuer, value in pairs:
        clusters.setdefault(issuer, []).append(value)
    keys = sorted(clusters)
    if len(keys) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        drawn: list[float] = []
        for _ in range(len(keys)):
            drawn.extend(clusters[keys[rng.randrange(len(keys))]])
        means.append(mean(drawn))
    means.sort()
    return (round(means[int(0.025 * (len(means) - 1))], 6),
            round(means[int(0.975 * (len(means) - 1))], 6))


def main() -> int:
    from dotenv import load_dotenv
    from examples.finder.lib.llm_io import chat_complete, make_chat_client, parse_llm_spec

    load_dotenv(ROOT / ".env")
    if not os.getenv("MARA_API_KEY"):
        raise SystemExit("MARA_API_KEY missing")

    retrieval = json.loads((BASE / "revised_exact_retrieval.json").read_text())["rows"]
    routing = json.loads((BASE / "revised_sdcr_routing.json").read_text())["rows"]
    covered = {r["candidate_id"]: bool(r["both_required_views_covered"]) for r in routing}

    # Assemble the served evidence per case. No new retrieval, no imputation.
    served: dict[str, dict] = {}
    for item in retrieval:
        cid = item["candidate_id"]
        if cid not in covered:
            raise SystemExit(f"routing outcome missing for {cid}; refusing to impute")
        source = "sdcr" if covered[cid] else "slot_only"
        served[cid] = {"source_arm": source, "evidence": item["arms"][source]["evidence"]}
        if not served[cid]["evidence"]:
            raise SystemExit(
                f"{cid}: fallback produced empty evidence from {source}; "
                "the policy is meant to guarantee non-empty evidence"
            )

    prior = json.loads(OUT.read_text()).get("rows", []) if OUT.exists() else []
    completed = {(r["candidate_id"], r["model"]) for r in prior}
    rows = list(prior)
    if completed:
        print(f"resuming: {len(completed)} completions already stored")

    clients = {}
    for model in MODELS:
        spec = parse_llm_spec("mara/" + model)
        clients[model] = (spec, make_chat_client(spec, transport="litellm"))

    for item in retrieval:
        cid = item["candidate_id"]
        nodes = served[cid]["evidence"]
        for model in MODELS:
            if (cid, model) in completed:
                continue
            spec, client = clients[model]
            receipts = []
            started = time.perf_counter()
            raw = chat_complete(
                client=client, model=spec.model, spec=spec, system=REF.SYSTEM,
                user=json.dumps(
                    {"question": item["question"], "evidence": REF.serialize(nodes)},
                    ensure_ascii=False),
                temperature=0, max_tokens=750, response_format={"type": "json_object"},
                label=f"fallback-answer-{cid}-{model}", max_attempts=2,
                receipt_sink=receipts.append)
            try:
                response = json.loads(raw)
            except json.JSONDecodeError:
                response = {"answer": raw, "parse_error": True}
            text = " ".join(str(response.get(k, "")) for k in
                            ("answer", "slot_1_answer", "slot_2_answer", "cross_view_conclusion"))
            slot_scores = [REF.f1(text, gold) for gold in item["golds"][:2]]
            gold_numbers = REF.nums(" ".join(item["golds"]))
            evidence_numbers = REF.nums(REF.serialize(nodes))
            answer_numbers = REF.nums(re.sub(r"\bE\d+\b", "", text, flags=re.I))
            rows.append({
                "candidate_id": cid, "issuer": item["issuer"], "model": model, "arm": ARM,
                "served_source_arm": served[cid]["source_arm"],
                "router_covered_required_views": covered[cid],
                "response": response,
                "slot_macro_f1": mean(slot_scores),
                "numeric_recall": (len(gold_numbers & answer_numbers) / len(gold_numbers)
                                   if gold_numbers else 0.0),
                "cross_view_f1": REF.f1(text, item["golds"][2]),
                "unsupported_numeric_rate": (len(answer_numbers - evidence_numbers) /
                                             len(answer_numbers) if answer_numbers else 0.0),
                "latency_seconds": time.perf_counter() - started,
                "receipt": [r.as_dict() for r in receipts],
            })
            FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps({"rows": rows}, indent=2, ensure_ascii=False) + "\n")
            print(f"  {cid} / {model} / from {served[cid]['source_arm']}: "
                  f"slot_f1={rows[-1]['slot_macro_f1']:.3f}")

    # Compare against the frozen arms, which used the identical harness.
    baseline = json.loads((BASE / "revised_answers.json").read_text())["rows"]
    fields = ("slot_macro_f1", "numeric_recall", "cross_view_f1", "unsupported_numeric_rate")
    summary: dict[str, dict] = {}
    for model in MODELS:
        mine = [r for r in rows if r["model"] == model]
        invalid = [r for r in mine if r["response"].get("parse_error")]

        def itt(row, field):
            return 0.0 if row["response"].get("parse_error") else row[field]

        summary[model] = {ARM: {f: round(mean(itt(r, f) for r in mine), 6) for f in fields}}
        summary[model][ARM].update({
            "latency_seconds": round(mean(r["latency_seconds"] for r in mine), 6),
            "schema_failure_rate": round(len(invalid) / len(mine), 6),
        })
        for other in ("sdcr", "centralized_single", "qualified_view_broadcast"):
            ref_rows = {r["candidate_id"]: r for r in baseline
                        if r["model"] == model and r["arm"] == other}
            if len(ref_rows) != len(mine):
                summary[model][f"vs_{other}"] = {
                    "status": f"skipped: {len(ref_rows)} baseline rows vs {len(mine)} new rows"}
                continue
            block = {}
            for field in ("slot_macro_f1", "numeric_recall", "cross_view_f1"):
                pairs = [(r["issuer"], itt(r, field) - itt(ref_rows[r["candidate_id"]], field))
                         for r in mine]
                lo, hi = clustered_bootstrap(pairs)
                block[field] = {
                    "delta": round(mean(v for _, v in pairs), 6),
                    "issuer_clustered_bootstrap_95_ci": [lo, hi],
                    "clusters": len({i for i, _ in pairs}),
                }
            summary[model][f"vs_{other}"] = block

    payload = {
        "contract": "log2026.fallback_answers.v1",
        "arm": ARM,
        "cases": len(retrieval),
        "models": list(MODELS),
        "harness": "prompt, schema, and scoring imported verbatim from 64_revised_answer_eval.py",
        "retrieval_reused": "evidence assembled from frozen revised_exact_retrieval.json arms",
        "fallback_policy": (
            "routed evidence when every required view was covered, otherwise the "
            "frozen TF-IDF top-2 capability team"
        ),
        "failure_policy": "schema failure receives zero in intention-to-treat",
        "router_hits": sum(1 for v in covered.values() if v),
        "router_misses": sum(1 for v in covered.values() if not v),
        "summary": summary,
        "rows": rows,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    lines = ["# Capability-Fallback Answer Evaluation (paid run)", "",
             f"- Cases: {len(retrieval)}  models: {', '.join(MODELS)}",
             f"- Router hits {payload['router_hits']}, misses {payload['router_misses']}", "",
             "| Model | Slot F1 (ITT) | Numeric recall | Cross-view F1 | Schema failure |",
             "|---|---:|---:|---:|---:|"]
    for model in MODELS:
        a = summary[model][ARM]
        lines.append(f"| {model} | {a['slot_macro_f1']:.3f} | {a['numeric_recall']:.3f} | "
                     f"{a['cross_view_f1']:.3f} | {a['schema_failure_rate']:.3f} |")
    lines += ["", "## Paired deltas vs the frozen arms (issuer-clustered bootstrap)", ""]
    for model in MODELS:
        for key, block in summary[model].items():
            if not key.startswith("vs_"):
                continue
            if "status" in block:
                lines.append(f"- {model} {key}: {block['status']}")
                continue
            stat = block["slot_macro_f1"]
            ci = stat["issuer_clustered_bootstrap_95_ci"]
            lines.append(f"- {model} {key} slot F1: {stat['delta']:+.6f} "
                         f"95% CI [{ci[0]:+.6f}, {ci[1]:+.6f}]")
    lines += ["", "Retrieval is shared with the frozen arms; only the served evidence arm "
                  "differs. Answer-level claims are limited to these 13 persona-screened "
                  "cases.", ""]
    (FALLBACK_DIR / "fallback_answers.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
