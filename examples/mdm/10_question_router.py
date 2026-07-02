#!/usr/bin/env python3
"""Question-type router over the MDM lanes — the medallion "routing, not
replacement" follow-up.

The 09 benchmark showed gold wins reference-data lookups (S1: ~federation
quality at 1/4 tokens, 0.2ms retrieval, lowest abstain) and loses narrative
questions (S2: 0.00). So: route each question to ONE lane and measure whether
the router captures gold's economy without paying its narrative blind spot.

Evaluation is a **$0 replay**: every lane's answer for every case is already
recorded in benchmark_aggregate.json — a routing policy just selects which
stored record counts. The only paid step is the optional LLM router
(12 one-line classification calls).

Policies compared (same 12 cases, same stored answers):

  always-federation / always-gold / always-best-silo   fixed baselines
  router-det@v1    deterministic, question-text only: metric keyword + explicit
                   year and no explanation marker -> gold, else federation
  router-llm       MARA classifier: REFERENCE_LOOKUP vs NARRATIVE
  oracle           per-case best lane — the routing CEILING (not achievable;
                   reported to show how much headroom routing has)

HONESTY (§20): the deterministic rule was designed AFTER seeing slice-level
results of run seocho-capital-v1 and is evaluated on those same 12 cases —
this is an EXPLORATORY analysis, labeled as such, not a confirmatory test.
Confirming it requires fresh held-out cases. The rule itself is versioned
below and uses ONLY the question text (never slice labels or stored scores).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
sys.path.insert(0, str(MDM_ROOT))
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

from dotenv import dotenv_values  # noqa: E402

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ.setdefault(k, v)

from examples.finder.lib import bench_common as bc  # noqa: E402
from examples.finder.lib import llm_io  # noqa: E402

ROUTER_VERSION = "router-det@v1"

# Concrete reported financial metrics (word-boundary matched).
_METRIC_KW = re.compile(
    r"\b(eps|margin|revenue|income|profit|cost|costs|ratio|debt|ocf|"
    r"cash flow|p/e)\b", re.IGNORECASE)
# Explicit reporting period: 4-digit year or FY-style token.
_YEAR_KW = re.compile(r"\b(?:fy\s?'?\d{2,4}|(?:19|20)\d{2})\b", re.IGNORECASE)
# The question asks for synthesis/explanation, not a stored figure.
_EXPLAIN_KW = re.compile(
    r"\b(driver|drivers|sustainab\w*|analysis|explain|why|how)\b", re.IGNORECASE)


def route_deterministic(query: str) -> str:
    """gold = 'a reported figure for a stated period'; everything else
    federates. Question text only — no labels, no peeking at scores."""
    if (_METRIC_KW.search(query) and _YEAR_KW.search(query)
            and not _EXPLAIN_KW.search(query)):
        return "gold"
    return "federation"


_LLM_ROUTER_SYSTEM = (
    "You are a query router for a financial data platform. Classify the "
    "question into exactly one class:\n"
    "- REFERENCE_LOOKUP: asks for specific reported figures/metrics for "
    "stated fiscal periods (answerable from a consolidated reference-data "
    "master of numeric facts)\n"
    "- NARRATIVE: asks for drivers, explanations, qualitative analysis, or "
    "synthesis across documents\n"
    'Return strict JSON: {"class": "REFERENCE_LOOKUP" | "NARRATIVE"}'
)


def route_llm(client, spec, query: str) -> tuple[str, str]:
    text = llm_io.chat_complete(
        client=client, model=spec.model, system=_LLM_ROUTER_SYSTEM,
        user=query, temperature=0.0, response_format={"type": "json_object"},
        label="router", spec=spec)
    parsed = llm_io.parse_json_lenient(text) or {}
    klass = str(parsed.get("class", "")).upper()
    lane = "gold" if klass == "REFERENCE_LOOKUP" else "federation"
    return lane, klass or f"parse_error:{text[:40]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-prefix", default="seocho-capital-v1")
    ap.add_argument("--llm", default="mara/DeepSeek-V3.1")
    ap.add_argument("--no-llm-router", action="store_true",
                    help="skip the paid 12-call LLM router; $0 replay only")
    args = ap.parse_args()

    out_dir = ROOT / "outputs" / "evaluation" / "mdm_demo" / args.run_prefix
    with open((out_dir / "benchmark_aggregate.json"), "r", encoding="utf-8") as f:
        bench = json.load(f)

    # case -> lane -> stored record
    by_case: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in bench["records"]:
        by_case[r["case_id"]][r["lane"]] = r
    cases = sorted(by_case)

    # --- routing decisions ---------------------------------------------------
    decisions: dict[str, dict[str, str]] = {c: {} for c in cases}
    for c in cases:
        q = by_case[c]["gold"]["query"]
        decisions[c]["router-det"] = route_deterministic(q)

    llm_router_cost = 0
    if not args.no_llm_router:
        spec = llm_io.parse_llm_spec(args.llm)
        client = llm_io.make_chat_client(spec)
        for c in cases:
            q = by_case[c]["gold"]["query"]
            lane, klass = route_llm(client, spec, q)
            decisions[c]["router-llm"] = lane
            decisions[c]["router-llm-class"] = klass
            llm_router_cost += 1

    # --- replay evaluation ($0) ----------------------------------------------
    def replay(select) -> dict:
        rs = [by_case[c][select(c)] for c in cases]
        ov = [r["evaluation"]["number_overlap_ratio"] for r in rs]
        return {
            "n": len(rs),
            "number_overlap_mean": round(sum(ov) / len(ov), 3),
            "abstain_rate": round(sum(1 for r in rs if r["abstain"]) / len(rs), 3),
            "context_chars_mean": int(sum(r["context_chars"] for r in rs) / len(rs)),
            "retrieval_ms_mean": round(sum(r["retrieval_ms"] for r in rs) / len(rs), 1),
            "lane_mix": dict(sorted(
                ((lane, sum(1 for c in cases if select(c) == lane))
                 for lane in {select(c) for c in cases}))),
        }

    def oracle_lane(c: str) -> str:
        lanes = by_case[c]
        return max(lanes, key=lambda l: (lanes[l]["evaluation"]["number_overlap_ratio"],
                                         -lanes[l]["context_chars"]))

    policies: dict[str, dict] = {
        "always-federation": replay(lambda c: "federation"),
        "always-gold": replay(lambda c: "gold"),
        "always-best-silo": replay(lambda c: "silo-compliance"),
        "router-det@v1": replay(lambda c: decisions[c]["router-det"]),
    }
    if not args.no_llm_router:
        policies["router-llm"] = replay(lambda c: decisions[c]["router-llm"])
    policies["oracle (ceiling)"] = replay(oracle_lane)

    agreement = {}
    for name, key in (("router-det@v1", "router-det"), ("router-llm", "router-llm")):
        if any(key in d for d in decisions.values()):
            agreement[name] = round(sum(
                1 for c in cases if decisions[c].get(key) == oracle_lane(c)) / len(cases), 3)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_prefix": args.run_prefix,
        "router_version": ROUTER_VERSION,
        "framing": "EXPLORATORY — rule designed after slice-level results of "
                   "this same run; confirmation needs held-out cases (§20.5)",
        "llm_router_calls": llm_router_cost,
        "policies": policies,
        "oracle_agreement": agreement,
        "decisions": {c: {**decisions[c], "oracle": oracle_lane(c),
                          "query": by_case[c]["gold"]["query"][:90]}
                      for c in cases},
    }
    out = out_dir / "router_aggregate.json"
    bc.atomic_write_json(out, payload)
    print(f"== wrote {out.relative_to(ROOT)} ==\n")
    print(f"{'policy':<20} | overlap | abstain | ctx chars | retr ms | lane mix")
    print("-" * 88)
    for name, v in policies.items():
        print(f"{name:<20} | {v['number_overlap_mean']:.3f}   | "
              f"{v['abstain_rate']:.2f}    | {v['context_chars_mean']:>9} | "
              f"{v['retrieval_ms_mean']:>7.1f} | {v['lane_mix']}")
    if agreement:
        print(f"\noracle agreement: {agreement}")
    print("\n(routing evaluation is a $0 replay of stored lane answers; "
          "EXPLORATORY per the framing field)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
