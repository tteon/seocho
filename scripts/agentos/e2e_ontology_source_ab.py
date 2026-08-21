"""Ontology-source A/B for text2cypher (seocho-ia4.13): is the ontology useful?

The sharp form of hadry's question — is the ontology genuinely meaningful to the
text2cypher agent? Both arms generate Cypher for the SAME questions with the SAME model
(MARA); the ONLY variable is the SCHEMA the prompt carries:

- THIN     : node labels only (an introspected/name-only view).
- DECLARED : the ontology-DECLARED schema (schema_for_prompt: labels + relationship
             directions/roles + cardinality/degree hints + properties).

Measured deterministically (no live DB needed — validate_text2cypher_fallback is static):
the SCHEMA-CONFORMANCE of the generated Cypher — does it invent labels/relationships/
properties that don't exist (hallucinated identifiers), omit tenant scope, or traverse
unbounded? If the ontology-declared schema produces materially fewer unknown-identifier
violations, the ontology is measurably useful to the generator.

Usage: python scripts/agentos/e2e_ontology_source_ab.py --llm mara/MiniMax-M2.7 \
    --out outputs/agentos/ontology_source_ab.json [--smoke N]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

import sys
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from seocho.query.hybrid_planner import policy_from_ontology, schema_for_prompt  # noqa: E402
from seocho.query.workload_compiler import validate_text2cypher_fallback  # noqa: E402

_QUESTIONS = [
    "Which regulations is Acme Corp subject to?",
    "Who enforces the GDPR regulation?",
    "List the compliance incidents reported by Globex.",
    "What control evidence mitigates the data-breach incident?",
    "Which policies govern Initech?",
    "Which regulator enforces the regulation that Acme violated?",
    "How many incidents relate to the AML regulation?",
    "What company is governed by the retention policy?",
]


def _load_env() -> None:
    for envf in [_ROOT / ".env", Path("/home/hadry/lab/seocho/.env")]:
        if envf.exists():
            for line in envf.read_text().splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if not k.strip().startswith(("NEO4J", "BOLT")):
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


def _ontology():
    path = _ROOT / "examples" / "finance-compliance" / "ontology.py"
    spec = importlib.util.spec_from_file_location("_fc_ontology", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_ontology()


def _thin_schema(onto) -> str:
    return "Node labels: " + ", ".join(sorted(onto.nodes.keys()))


def _declared_schema(onto, policy) -> str:
    s = schema_for_prompt(onto, policy)
    # schema_for_prompt returns a dict; render it compactly for the prompt
    return json.dumps({k: (list(v) if isinstance(v, tuple) else v) for k, v in s.items()},
                      default=str, indent=0)[:4000]


def _client(llm):
    import openai
    return openai.OpenAI(api_key=os.environ["MARA_API_KEY"],
                         base_url="https://api.cloud.mara.com/v1"), llm.split("/", 1)[1]


def _gen(client, model, schema_text, question) -> str:
    system = (
        "You translate the question into ONE Cypher query for a tenant-scoped graph.\n"
        "Use ONLY the schema below. Bind the tenant with a `$workspace_id` parameter on "
        "the anchor node and end with `LIMIT $limit`.\n\n" + schema_text + "\n\n"
        'Return JSON: {"cypher": "..."}'
    )
    try:
        r = client.chat.completions.create(
            model=model, temperature=0.0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": question}])
        raw = re.sub(r"^```(json)?|```$", "", (r.choices[0].message.content or "{}").strip(),
                     flags=re.MULTILINE).strip()
        return json.loads(raw).get("cypher", "") or ""
    except Exception as e:
        print(f"    gen error: {type(e).__name__} {str(e)[:80]}")
        return ""


_HALLUCINATION = ("unknown_labels", "unknown_relationships", "unknown_properties")


def _score(cypher, policy) -> Dict[str, Any]:
    if not cypher.strip():
        return {"cypher": "", "violations": ["empty"], "hallucinated": True, "conformant": False}
    viol = validate_text2cypher_fallback(cypher, params={"workspace_id": "acme", "limit": 1}, policy=policy)
    hallucinated = any(v.split(":")[0] in _HALLUCINATION for v in viol)
    return {"cypher": cypher, "violations": list(viol),
            "hallucinated": hallucinated, "conformant": len(viol) == 0}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--llm", default="mara/MiniMax-M2.7")
    ap.add_argument("--smoke", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    _load_env()
    onto = _ontology()
    policy = policy_from_ontology(onto)
    client, model = _client(args.llm)
    qs = _QUESTIONS[: args.smoke] if args.smoke else _QUESTIONS

    arms = {"THIN": _thin_schema(onto), "DECLARED": _declared_schema(onto, policy)}
    results: Dict[str, Any] = {}
    for arm, schema in arms.items():
        print(f"=== {arm} ({len(qs)} questions) ===")
        scored: List[Dict[str, Any]] = []
        for i, q in enumerate(qs):
            s = _score(_gen(client, model, schema, q), policy)
            scored.append(s)
            print(f"  [{i}] conformant={s['conformant']} hallucinated={s['hallucinated']} "
                  f"viol={s['violations'][:2]}")
        n = len(scored)
        results[arm] = {
            "n": n,
            "conformance_rate": round(sum(1 for s in scored if s["conformant"]) / n, 3),
            "hallucination_rate": round(sum(1 for s in scored if s["hallucinated"]) / n, 3),
            "avg_violations": round(sum(len(s["violations"]) for s in scored) / n, 2),
            "per_q": scored,
        }

    report = {"model": args.llm, "questions": len(qs), "arms": {k: {kk: vv for kk, vv in v.items() if kk != "per_q"} for k, v in results.items()}, "detail": results}
    print("\n=== ontology-source A/B (seocho-ia4.13) ===")
    print(f"  {'metric':22s} {'THIN':>8s} {'DECLARED':>10s}")
    for m in ("conformance_rate", "hallucination_rate", "avg_violations"):
        print(f"  {m:22s} {results['THIN'][m]:>8} {results['DECLARED'][m]:>10}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
