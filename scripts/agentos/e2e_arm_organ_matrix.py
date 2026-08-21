"""arm×organ A/B: the actual agent-OS experiment.

Runs the 7-arm ablation (BARE, GOVERNED, and the five leave-one-outs) over the live
erb cross-source graph (erblpg) and reports TWO planes:

  Plane-1 (mechanism, deterministic, per arm × question): what each organ DID —
    schema source (pinned vs introspected), workspace enforcement, guardrail
    rejections, repair attempts, rows returned, honest-abstain source. These are the
    OS-mechanism signals; they do not need an LLM to be true.

  Plane-2 (answer quality, cross-vendor judge, N=9 descriptive): a DeepSeek judge
    scores each answer's faithfulness to the gold answer_facts (gpt-oss generates,
    DeepSeek judges — cross-vendor, so it is not self-grading).

The five organs are runtime flags (query.arm_config.ArmConfig); the schema/pin organ
needs the RCU stack (snapshot store + active pointer + pin registry + resolver) wired
onto the engine, which `wire_rcu` does here so "GOVERNED reads ONE pinned frozen
schema per request" is real at query time, not a class that is never constructed.

Usage: python scripts/agentos/e2e_arm_organ_matrix.py [--gen mara/gpt-oss-120b]
       [--judge mara/DeepSeek-V3.1] [--database erblpg] [--workspace erb]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
_WS_DIR = os.path.join(_ROOT, "outputs", "agentos", "erb_xsource_workingset")
_SNAP_DIR = os.path.join(_ROOT, "outputs", "agentos", "_rcu")


def _load_mara() -> None:
    for envf in [os.path.join(_ROOT, ".env"), "/home/hadry/lab/seocho/.env"]:
        if os.path.exists(envf):
            for line in open(envf):
                if line.startswith(("MARA_API_KEY=", "MARA_BASE_URL=")) and "=" in line:
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k, v.strip().strip('"').strip("'"))
            return


def _ontology():
    spec = importlib.util.spec_from_file_location(
        "idx", os.path.join(_ROOT, "scripts", "agentos", "e2e_index_workingset.py"))
    idx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(idx)
    return idx._ontology()


def wire_rcu(client, ontology, workspace_id: str) -> str:
    """Wire the RCU / pinned-schema stack onto the live engine so the schema+pin
    organs actually resolve a FROZEN snapshot per request (they are None by default).
    Returns the schema_source the governed arm now resolves ('pinned' if it worked)."""
    from seocho.ontology.active_pointer import ActiveOntologyPointer
    from seocho.ontology.snapshot_store import OntologySnapshotStore
    from seocho.ontology.version_pin import VersionPinRegistry
    from seocho.query.pinned_schema import PinnedSchemaResolver

    os.makedirs(_SNAP_DIR, exist_ok=True)
    store = OntologySnapshotStore(os.path.join(_SNAP_DIR, "snapshots"))
    store.save(ontology)                                   # erb / 1.0.0
    ptr = ActiveOntologyPointer(os.path.join(_SNAP_DIR, "active.db"))
    fp = ontology.schema_fingerprint()
    # first publish (idempotent-ish: ignore "already exists")
    ptr.publish(workspace_id, ontology.package_id, version=ontology.version,
                fingerprint=fp, fencing_token=1)
    reg = VersionPinRegistry(ptr)

    eng = client._engine
    eng._pinned_schema_resolver = PinnedSchemaResolver(store)
    eng._ontology_pin_registry = reg
    eng._active_ontology_pointer = ptr
    eng._ontology_package_id = ontology.package_id
    return fp


_JUDGE_SYS = (
    "You grade whether an ANSWER faithfully conveys the required FACTS. Output ONLY a "
    "JSON object: {\"coverage\": <0..1>, \"unsupported\": <true|false>}. coverage = "
    "fraction of the required facts the answer correctly states; unsupported = the "
    "answer asserts something that contradicts or is absent from the facts (a "
    "confabulation). An honest 'no evidence / cannot answer' is coverage 0, "
    "unsupported false (abstaining is not confabulating)."
)


def judge(judge_llm, question: str, answer: str, facts) -> dict:
    from seocho.store.llm import complete_with_task_hints
    user = (f"QUESTION: {question}\n\nREQUIRED FACTS:\n"
            + "\n".join(f"- {f}" for f in facts)
            + f"\n\nANSWER TO GRADE:\n{answer}")
    try:
        resp = complete_with_task_hints(
            judge_llm, system=_JUDGE_SYS, user=user, temperature=0.0,
            response_format={"type": "json_object"}, reasoning_mode=False,
            task_hint="json_extraction")
        data = resp.json() if hasattr(resp, "json") else {}
        if not isinstance(data, dict):
            data = {}
    except Exception as e:
        m = re.search(r'"coverage"\s*:\s*([0-9.]+)', str(e))
        data = {"coverage": float(m.group(1)) if m else 0.0, "unsupported": False,
                "judge_error": type(e).__name__}
    cov = data.get("coverage", 0.0)
    try:
        cov = max(0.0, min(1.0, float(cov)))
    except (TypeError, ValueError):
        cov = 0.0
    return {"coverage": cov, "unsupported": bool(data.get("unsupported", False)),
            **({"judge_error": data["judge_error"]} if "judge_error" in data else {})}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gen", default="mara/gpt-oss-120b")
    ap.add_argument("--judge", default="mara/DeepSeek-V3.1")
    ap.add_argument("--uri", default="bolt://localhost:17687")
    ap.add_argument("--password", default="h0gatepass")
    ap.add_argument("--database", default="erblpg")
    ap.add_argument("--workspace", default="erb")
    ap.add_argument("--limit-q", type=int, default=0, help="only first N questions (0=all)")
    args = ap.parse_args()
    _load_mara()

    from seocho import Seocho
    from seocho.query.arm_config import ablation_arms

    ontology = _ontology()
    client = Seocho.local(
        ontology, llm=args.gen, graph=args.uri, neo4j_user="neo4j",
        neo4j_password=args.password, api_key=os.environ.get("MARA_API_KEY"),
        workspace_id=args.workspace)
    fp = wire_rcu(client, ontology, args.workspace)
    print(f"gen={args.gen} judge={args.judge} db={args.database} ws={args.workspace} fp={fp[:8]}")

    # judge backend (cross-vendor): "mara/DeepSeek-V3.1" -> provider=mara, model=...
    from seocho.store.llm import create_llm_backend
    j_provider, _, j_model = args.judge.partition("/")
    judge_llm = create_llm_backend(
        provider=j_provider, model=j_model or None, api_key=os.environ.get("MARA_API_KEY"))

    questions = []
    with open(os.path.join(_WS_DIR, "questions.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    if args.limit_q:
        questions = questions[: args.limit_q]

    # sanity: confirm the pin/schema organ is actually live now
    client._engine._structured_arm = ablation_arms()[1]      # governed
    client.ask(questions[0]["question"], engine="structured", database=args.database)
    src0 = client.last_query_metadata["structured"]["schema_source"]
    print(f"governed schema_source (pin/schema organ live?): {src0}")

    results = []
    for arm in ablation_arms():
        client._engine._structured_arm = arm
        per_q = []
        for q in questions:
            try:
                ans = client.ask(q["question"], engine="structured", database=args.database)
                md = client.last_query_metadata
                st = md["structured"]
                verdict = judge(judge_llm, q["question"], ans, q.get("answer_facts", []))
                per_q.append({
                    "qid": q["question_id"], "answer_source": md["answer_source"],
                    "rows": md["result_count"], "schema_source": st["schema_source"],
                    "ws_enforced": st["workspace_enforced"], "guardrail_on": st["guardrail_on"],
                    "guardrail_rejected": st["guardrail_rejected"],
                    "violations": st["guardrail_violations"], "repairs": st["repair_attempts"],
                    "coverage": verdict["coverage"], "unsupported": verdict["unsupported"],
                    "cypher": st["cypher"][:200], "answer": str(ans)[:300],
                })
            except Exception as e:
                per_q.append({"qid": q["question_id"], "error": f"{type(e).__name__}: {str(e)[:160]}"})
        ok = [r for r in per_q if "error" not in r]
        n = len(ok) or 1
        summary = {
            "arm": arm.name, "organs_on": arm.organs_on(),
            "n": len(ok), "errors": len(per_q) - len(ok),
            "mean_coverage": round(sum(r["coverage"] for r in ok) / n, 3),
            "confabulations": sum(1 for r in ok if r["unsupported"]),
            "abstains": sum(1 for r in ok if r["answer_source"] != "structured"),
            "guardrail_rejects": sum(1 for r in ok if r.get("guardrail_rejected")),
            "pinned_schema": sum(1 for r in ok if r.get("schema_source") == "pinned"),
            "ws_enforced": sum(1 for r in ok if r.get("ws_enforced")),
        }
        results.append({"summary": summary, "per_q": per_q})
        print(f"\n### {arm.name:20s} organs={arm.organs_on()}")
        print(f"    coverage={summary['mean_coverage']} confab={summary['confabulations']} "
              f"abstain={summary['abstains']} gr_reject={summary['guardrail_rejects']} "
              f"pinned={summary['pinned_schema']}/{summary['n']} ws_enf={summary['ws_enforced']}/{summary['n']} "
              f"err={summary['errors']}")

    out = os.path.join(_WS_DIR, "arm_organ_results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"gen": args.gen, "judge": args.judge, "database": args.database,
                   "workspace": args.workspace, "fingerprint": fp, "arms": results}, fh, indent=2)
    print(f"\n=== wrote {out} ===")
    print("\n=== Plane-1 (mechanism) + Plane-2 (coverage) summary ===")
    print(f"{'arm':22s} {'cov':>5s} {'confab':>6s} {'abst':>5s} {'gr_rej':>6s} {'pinned':>7s} {'ws_enf':>6s}")
    for r in results:
        s = r["summary"]
        print(f"{s['arm']:22s} {s['mean_coverage']:5.2f} {s['confabulations']:6d} "
              f"{s['abstains']:5d} {s['guardrail_rejects']:6d} "
              f"{str(s['pinned_schema'])+'/'+str(s['n']):>7s} {str(s['ws_enforced'])+'/'+str(s['n']):>6s}")


if __name__ == "__main__":
    main()
