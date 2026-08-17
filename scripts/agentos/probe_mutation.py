"""Probe 2 — ontology mutation mid-run (pin/RCU organ load-bearing test, seocho-8qp).

The RCU claim: the OS delivers ONE frozen ontology version per request, so a publish /
in-place mutation landing mid-run cannot make the prompt schema and the guardrail policy
disagree (B3 consistency). Without the pin, the un-pinned path reads the LIVE ontology
object for its guardrail policy while its prompt schema comes from DB introspection —
mutate the live ontology mid-stream and the two disagree: the generator keeps emitting
the (still-correct-for-the-graph) v1 identifiers, the policy now only allows v2 names,
and every valid query is spuriously rejected.

Design (paired, deterministic):
  phase A: ask K fact-retrieval questions under governed (pin ON) and governed-no-pin.
  MUTATE:  in-place rename of relationship types on the LIVE ontology object
           (TREATED_BY->HAS_THERAPY, HAS_SYMPTOM->SHOWS_SIGN) — the anti-pattern RCU
           exists to guard against. The graph itself is untouched (still v1-shaped).
  phase B: ask the SAME K questions again under both arms.

Expected: governed pre==post (frozen snapshot for prompt AND policy — immune);
governed-no-pin post collapses to guardrail rejections/abstains. The delta-of-deltas is
the pin organ's measured contribution.

Usage: python scripts/agentos/probe_mutation.py [--k 5] [--gen mara/gpt-oss-120b]
       [--judge mara/DeepSeek-V3.1] [--database medicallpg]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
_QUESTIONS = "/home/hadry/openup/_graphrag_benchmark/Datasets/Questions/medical_questions.json"

_matrix_spec = importlib.util.spec_from_file_location(
    "matrix", os.path.join(_ROOT, "scripts", "agentos", "e2e_arm_organ_matrix.py"))
_matrix = importlib.util.module_from_spec(_matrix_spec)
_matrix_spec.loader.exec_module(_matrix)
wire_rcu, judge, _load_mara = _matrix.wire_rcu, _matrix.judge, _matrix._load_mara

_med_spec = importlib.util.spec_from_file_location(
    "medidx", os.path.join(_ROOT, "scripts", "agentos", "index_graphrag_medical.py"))
_medidx = importlib.util.module_from_spec(_med_spec)
_med_spec.loader.exec_module(_medidx)
medical_ontology = _medidx.medical_ontology

RENAMES = {"TREATED_BY": "HAS_THERAPY", "HAS_SYMPTOM": "SHOWS_SIGN"}


def mutate_live_ontology(onto) -> None:
    """In-place mid-run mutation of the SHARED live ontology object — the concurrent
    publisher landing without RCU discipline. The graph stays v1-shaped."""
    for old, new in RENAMES.items():
        if old in onto.relationships:
            onto.relationships[new] = onto.relationships.pop(old)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--gen", default="mara/gpt-oss-120b")
    ap.add_argument("--judge", default="mara/DeepSeek-V3.1")
    ap.add_argument("--uri", default="bolt://localhost:17687")
    ap.add_argument("--password", default="h0gatepass")
    ap.add_argument("--database", default="medicallpg")
    args = ap.parse_args()
    _load_mara()

    from seocho import Seocho
    from seocho.query.arm_config import ArmConfig
    from seocho.store.llm import create_llm_backend

    # deterministic fact-retrieval sample (treatment/symptom questions exercise the
    # renamed relationships)
    qs = [q for q in sorted(json.load(open(_QUESTIONS)), key=lambda x: x["id"])
          if q["question_type"] == "Fact Retrieval"
          and any(w in q["question"].lower() for w in ("treat", "therapy", "symptom"))][: args.k]
    print(f"k={len(qs)} questions gen={args.gen}", flush=True)

    jp, _, jm = args.judge.partition("/")
    judge_llm = create_llm_backend(provider=jp, model=jm or None,
                                   api_key=os.environ.get("MARA_API_KEY"))

    def run_phase(client, arm, phase):
        client._engine._structured_arm = arm
        out = []
        for q in qs:
            try:
                ans = client.ask(q["question"], engine="structured", database=args.database)
                md = client.last_query_metadata
                st = md["structured"]
                v = judge(judge_llm, q["question"], ans, [q["answer"]])
                out.append({"qid": q["id"], "phase": phase,
                            "coverage": v["coverage"],
                            "answered": md["answer_source"] == "structured",
                            "guardrail_rejected": st["guardrail_rejected"],
                            "violations": list(st["guardrail_violations"])[:3],
                            "schema_source": st["schema_source"],
                            "cypher": st["cypher"][:140], "answer": str(ans)[:160]})
            except Exception as e:
                out.append({"qid": q["id"], "phase": phase,
                            "error": f"{type(e).__name__}: {str(e)[:120]}"})
        return out

    results = {}
    # SEPARATE clients per arm so each has its own live ontology object to mutate —
    # the pinned arm's resolver reads the frozen snapshot either way.
    for arm in (ArmConfig.governed(), ArmConfig.governed().without("pin")):
        onto = medical_ontology()
        client = Seocho.local(
            onto, llm=args.gen, graph=args.uri, neo4j_user="neo4j",
            neo4j_password=args.password, api_key=os.environ.get("MARA_API_KEY"),
            workspace_id="med")
        wire_rcu(client, onto, "med")

        pre = run_phase(client, arm, "pre")
        mutate_live_ontology(onto)                      # << the mid-run mutation
        post = run_phase(client, arm, "post")

        def _agg(rows):
            ok = [r for r in rows if "error" not in r]
            n = len(ok) or 1
            return {"n": len(ok),
                    "coverage": round(sum(r["coverage"] for r in ok) / n, 3),
                    "answered": sum(1 for r in ok if r["answered"]),
                    "rejected": sum(1 for r in ok if r["guardrail_rejected"]),
                    "errors": len(rows) - len(ok)}

        results[arm.name] = {"pre": _agg(pre), "post": _agg(post), "detail": pre + post}
        a, b = results[arm.name]["pre"], results[arm.name]["post"]
        print(f"\n### {arm.name}", flush=True)
        print(f"    pre : cov={a['coverage']} answered={a['answered']}/{a['n']} rejected={a['rejected']}",
              flush=True)
        print(f"    post: cov={b['coverage']} answered={b['answered']}/{b['n']} rejected={b['rejected']}",
              flush=True)

    out = os.path.join(_ROOT, "outputs", "agentos", "probe_mutation_results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"renames": RENAMES, "k": len(qs), "gen": args.gen,
                   "judge": args.judge, "database": args.database, "arms": results},
                  fh, indent=2)
    print(f"\n=== wrote {out} ===", flush=True)
    g, np_ = results.get("governed", {}), results.get("governed-no-pin", {})
    if g and np_:
        dd = ((g["post"]["coverage"] - g["pre"]["coverage"])
              - (np_["post"]["coverage"] - np_["pre"]["coverage"]))
        print(f"delta-of-deltas (pin organ contribution under mutation): {round(dd, 3)}",
              flush=True)


if __name__ == "__main__":
    main()
