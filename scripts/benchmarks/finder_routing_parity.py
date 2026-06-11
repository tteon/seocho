#!/usr/bin/env python3
"""Synergy #2 live: routed model tiers vs all-frontier on FinDER (seocho-jdg).

Runs the REAL wired path: SEOCHO_MODEL_ROUTING=1 routes extraction/linking to
the BALANCED tier and answer synthesis to FRONTIER at the
complete_with_task_hints chokepoint; the all-frontier arm keeps routing OFF.
Counts calls per model (relative cost) and compares answer quality.

Measured 2026-06-11 (MARA + DozerDB, FinDER tutorial subset, n=4):
  all_frontier: 14x MiniMax-M2.7            rel_cost=140  contains=0.50 numeric_recall=0.75
  routed:        8x M2.5 + 6x M2.7          rel_cost= 84  contains=0.50 numeric_recall=0.88
  -> cost 0.60x (40% saving) at equal-or-better answer quality.

Needs DozerDB (NEO4J_PASSWORD) + MARA_API_KEY. Run:
  python3 scripts/benchmarks/finder_routing_parity.py
"""
import os, re, sys, time
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "src", ROOT / "extraction"):
    sys.path.insert(0, str(p))

key = None
for line in open(ROOT / ".env", encoding="utf-8"):
    m = re.match(r'\s*MARA_API_KEY\s*=\s*"?([^"\n]+)"?', line)
    if m: key = m.group(1).strip()
os.environ.setdefault("MARA_API_KEY", key or "")

from seocho import NodeDef, Ontology, P, RelDef, Seocho
from seocho.benchmarking import load_finder_cases, compare_answers, score_answer_slots
from seocho.routing import ModelTier, ModelRouter
import seocho.store.llm as llm_mod

REL_COST = {"DeepSeek-V3.1": 1.0, "MiniMax-M2.5": 3.0, "MiniMax-M2.7": 10.0}

def onto():
    return Ontology(
        name="parity",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True), "sector": P(str)}),
            "FinancialMetric": NodeDef(properties={"name": P(str, unique=True), "value": P(str), "year": P(str)}),
            "Risk": NodeDef(properties={"name": P(str, unique=True), "category": P(str)}),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric"),
            "FACES": RelDef(source="Company", target="Risk"),
        },
    )

cases = load_finder_cases(ROOT / "examples/finder/datasets/finder_tutorial_subset.json")[:4]

# instrument: count which model each completion actually used
calls = Counter()
_orig = llm_mod.complete_with_task_hints
def counting(llm, **kw):
    resp = _orig(llm, **kw)
    routed = kw.get("model") or llm_mod._env_routed_model(llm, kw.get("task_hint"))
    calls[routed or getattr(llm, "model", "?")] += 1
    return resp
llm_mod.complete_with_task_hints = counting
# local_engine imported the symbol directly — patch there too
import seocho.local_engine as le
le.complete_with_task_hints = counting
import seocho.query.answering as ans
ans.complete_with_task_hints = counting
import seocho.index.extraction_engine as xe
xe.complete_with_task_hints = counting

def run_arm(name, routing_on, db):
    os.environ["SEOCHO_MODEL_ROUTING"] = "1" if routing_on else ""
    calls.clear()
    client = Seocho.local(onto(), llm="mara/MiniMax-M2.7", graph="bolt://localhost:7687",
                          neo4j_user="neo4j", neo4j_password=os.getenv("NEO4J_PASSWORD", "seocho-dev"), api_key=key)
    gs = client._engine.graph_store
    try:
        gs.ensure_database(db)
    except Exception:
        pass
    rows = []
    try:
        for c in cases:
            client.add(c.text, database=db, category=str(c.category or "memory"))
        for c in cases:
            t0 = time.perf_counter()
            try:
                a = client.ask(c.question, database=db)
            except Exception as e:
                a = f"(error {type(e).__name__})"
            dt = (time.perf_counter() - t0) * 1000
            _, contains = compare_answers(c.expected_answer, a)
            slots = score_answer_slots(c.expected_answer, a)
            rows.append({"contains": bool(contains),
                         "numeric_recall": float(slots.get("numeric_recall", 0.0)),
                         "ms": dt})
    finally:
        try:
            gs.query("MATCH (n) WHERE n._source_id IS NOT NULL DETACH DELETE n", database=db)
        except Exception:
            pass
        client.close()
    cost = sum(REL_COST.get(m, 10.0) * n for m, n in calls.items())
    return {
        "arm": name,
        "model_calls": dict(calls),
        "relative_cost": cost,
        "contains_rate": sum(r["contains"] for r in rows) / len(rows),
        "numeric_recall": sum(r["numeric_recall"] for r in rows) / len(rows),
        "p50_ms": sorted(r["ms"] for r in rows)[len(rows)//2],
    }

a = run_arm("all_frontier", routing_on=False, db="paritybencha")
b = run_arm("routed", routing_on=True, db="paritybenchb")

print("=" * 64)
print("SYNERGY #2 LIVE — routed tiers vs all-frontier (MARA + DozerDB)")
print("=" * 64)
for r in (a, b):
    print(f"  [{r['arm']:12s}] calls={r['model_calls']}  rel_cost={r['relative_cost']:.0f}")
    print(f"                 contains={r['contains_rate']:.2f}  numeric_recall={r['numeric_recall']:.2f}  p50={r['p50_ms']:.0f}ms")
ratio = b["relative_cost"] / a["relative_cost"] if a["relative_cost"] else 1.0
print(f"\n  routed/all-frontier cost ratio = {ratio:.2f}x "
      f"({'<0.6x MET' if ratio < 0.6 else 'above 0.6x'})")
print(f"  support parity: contains {a['contains_rate']:.2f} -> {b['contains_rate']:.2f}, "
      f"numeric_recall {a['numeric_recall']:.2f} -> {b['numeric_recall']:.2f}")
