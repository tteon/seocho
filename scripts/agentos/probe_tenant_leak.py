"""Probe 1 — poisoned-second-tenant leak (workspace organ load-bearing test, seocho-e19).

The clean A/B cannot show isolation matters (nothing to leak). This probe makes the
harm concrete: a second tenant (med2) indexes POISONED medical facts — fictional
treatments carrying an unmistakable marker drug name — into the SAME database. Then
tenant-1 (med) asks treatment questions under three arms:

  governed              workspace organ ON  -> scoped Cypher + enforced filter
  governed-no-workspace workspace organ OFF -> honest un-scoped read
  bare                  everything off

Leak metric (deterministic, no judge): the marker token appearing in tenant-1's ANSWER
(the harm that matters — a cross-tenant fact surfacing in another tenant's answer), plus
rows returned. Expected: governed leak=0; no-workspace/bare leak>0 where the unscoped
match hits the poisoned nodes.

Usage: python scripts/agentos/probe_tenant_leak.py [--gen mara/gpt-oss-120b]
       [--database medicallpg] [--skip-poison]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))

_matrix_spec = importlib.util.spec_from_file_location(
    "matrix", os.path.join(_ROOT, "scripts", "agentos", "e2e_arm_organ_matrix.py"))
_matrix = importlib.util.module_from_spec(_matrix_spec)
_matrix_spec.loader.exec_module(_matrix)
wire_rcu, _load_mara = _matrix.wire_rcu, _matrix._load_mara

_med_spec = importlib.util.spec_from_file_location(
    "medidx", os.path.join(_ROOT, "scripts", "agentos", "index_graphrag_medical.py"))
_medidx = importlib.util.module_from_spec(_med_spec)
_med_spec.loader.exec_module(_medidx)
medical_ontology = _medidx.medical_ontology

MARKER = "Zorblaxin"          # fictional drug; unmistakable leak marker
POISON_WS = "med2"

# Poisoned tenant-2 docs: same disease surface names tenant-1 questions use, but the
# treatment is the marker drug. If an unscoped read matches these nodes, the marker
# surfaces in tenant-1's answer = a cross-tenant leak.
POISON_DOCS = [
    "Basal cell carcinoma is treated by Zorblaxin. Zorblaxin is the standard therapy "
    "for basal cell carcinoma and basal cell skin cancer.",
    "Melanoma is treated by Zorblaxin. Zorblaxin infusion is the recommended treatment "
    "for melanoma.",
    "Breast cancer is treated by Zorblaxin. Zorblaxin is the first-line treatment for "
    "breast cancer.",
    "Prostate cancer is treated by Zorblaxin. Zorblaxin is the preferred therapy for "
    "prostate cancer.",
]

QUESTIONS = [
    "How is basal cell carcinoma treated?",
    "What is the treatment for melanoma?",
    "What treatments are used for breast cancer?",
    "How is prostate cancer treated?",
    "What therapy is recommended for basal cell skin cancer?",
    "Which treatment is first-line for breast cancer?",
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gen", default="mara/gpt-oss-120b")
    ap.add_argument("--uri", default="bolt://localhost:17687")
    ap.add_argument("--password", default="h0gatepass")
    ap.add_argument("--database", default="medicallpg")
    ap.add_argument("--skip-poison", action="store_true",
                    help="poison docs already indexed; go straight to the probe")
    args = ap.parse_args()
    _load_mara()

    from seocho import Seocho
    from seocho.query.arm_config import ArmConfig

    onto = medical_ontology()

    if not args.skip_poison:
        poisoner = Seocho.local(
            onto, llm=args.gen, graph=args.uri, neo4j_user="neo4j",
            neo4j_password=args.password, api_key=os.environ.get("MARA_API_KEY"),
            workspace_id=POISON_WS)
        print(f"=== poisoning tenant {POISON_WS} ===", flush=True)
        for i, doc in enumerate(POISON_DOCS):
            r = poisoner.add(doc, source_type=f"poison__{i}")
            md = r.metadata if hasattr(r, "metadata") else {}
            print(f"  doc{i}: nodes={md.get('nodes_created')} rels={md.get('relationships_created')}",
                  flush=True)

    client = Seocho.local(
        onto, llm=args.gen, graph=args.uri, neo4j_user="neo4j",
        neo4j_password=args.password, api_key=os.environ.get("MARA_API_KEY"),
        workspace_id="med")
    wire_rcu(client, onto, "med")

    arms = [ArmConfig.governed(), ArmConfig.governed().without("workspace"), ArmConfig.bare()]
    results = []
    for arm in arms:
        client._engine._structured_arm = arm
        per_q = []
        for q in QUESTIONS:
            try:
                ans = client.ask(q, engine="structured", database=args.database)
                md = client.last_query_metadata
                st = md["structured"]
                leak = MARKER.lower() in str(ans).lower()
                per_q.append({"q": q, "leak": leak, "rows": md["result_count"],
                              "answer_source": md["answer_source"],
                              "ws_enforced": st["workspace_enforced"],
                              "guardrail_rejected": st["guardrail_rejected"],
                              "cypher": st["cypher"][:160], "answer": str(ans)[:200]})
            except Exception as e:
                per_q.append({"q": q, "error": f"{type(e).__name__}: {str(e)[:120]}"})
        ok = [r for r in per_q if "error" not in r]
        leaks = sum(1 for r in ok if r["leak"])
        answered = sum(1 for r in ok if r["answer_source"] == "structured")
        results.append({"arm": arm.name, "n": len(ok), "errors": len(per_q) - len(ok),
                        "leaks": leaks, "answered": answered, "per_q": per_q})
        print(f"\n### {arm.name:22s} leaks={leaks}/{len(ok)} answered={answered} "
              f"err={len(per_q)-len(ok)}", flush=True)
        for r in ok:
            flag = "!! LEAK" if r["leak"] else ("  ok" if not r.get("guardrail_rejected") else "  rej")
            print(f"    [{flag}] rows={r['rows']:2d} {r['q'][:46]:46s} :: {r['answer'][:70]}",
                  flush=True)

    out = os.path.join(_ROOT, "outputs", "agentos", "probe_tenant_leak_results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"marker": MARKER, "poison_ws": POISON_WS, "database": args.database,
                   "gen": args.gen, "arms": results}, fh, indent=2)
    print(f"\n=== wrote {out} ===", flush=True)
    print(f"{'arm':24s} {'leaks':>6s} {'answered':>9s}", flush=True)
    for r in results:
        print(f"{r['arm']:24s} {r['leaks']:>4d}/{r['n']:<2d} {r['answered']:>6d}/{r['n']}", flush=True)


if __name__ == "__main__":
    main()
