"""Cold-start extraction A/B: pure-open vs upper-anchored bootstrap (seocho-ia4.11).

Tests the ADR-0179 design on a REAL, instance-diverse corpus (FinDER tutorial subset:
10 docs, ~10 distinct companies, recurring TYPES — Company/Person/Metric/Risk). Both
arms do real MARA LLM extraction over the SAME corpus; the ONLY variable is the
extraction CONTEXT:

- PURE_OPEN  : generic "extract entities+relationships, name types freely" (SEOCHO's
               current no-ontology path — empty type slots -> vocabulary drift).
- BOOTSTRAP  : render_upper_frame() (small abstract upper ontology) + require an
               `upper` anchor per entity + a running-vocabulary soft hint (reuse the
               specific types seen so far). Design: cold-start-schema-bootstrap.

Measured (the design's three questions):
- DRIFT       : distinct entity-type labels + normalized-type spread (raw/normalized;
                higher = more surface synonyms of one concept). Lower is better IF
                recall holds.
- AXIOM-SUPPORT: induce_ontology_from_graph -> induced types (with broader=upper) +
                mined axioms. Does a clean, hierarchical ontology fall out?
- RECALL      : nodes / relationships / distinct entity names / company coverage.
                The key hypothesis: a SMALL abstract frame does NOT suppress recall
                the way a rich ontology does (the "Anchor, Don't Name" firewall re-test).

Usage:
  python scripts/agentos/e2e_cold_start_ab.py --llm mara/MiniMax-M2.7 \
      --out outputs/agentos/cold_start_ab.json [--smoke N]
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict

import sys
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from seocho.ontology.induce import induce_ontology_from_graph, induction_report  # noqa: E402
from seocho.ontology.upper import render_upper_frame  # noqa: E402

_CORPUS = "examples/finder/datasets/finder_tutorial_subset.json"
_EXPECTED_COMPANIES = ["apple", "microsoft", "nvidia", "alphabet", "meta",
                       "tesla", "jpmorgan", "berkshire", "amazon"]


def _load_env() -> None:
    for envf in [_ROOT / ".env", Path("/home/hadry/lab/seocho/.env")]:
        if envf.exists():
            for line in envf.read_text().splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if not k.strip().startswith(("NEO4J", "BOLT")):
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


def _client(llm: str):
    import openai
    if llm.startswith("mara/"):
        return openai.OpenAI(api_key=os.environ["MARA_API_KEY"],
                             base_url="https://api.cloud.mara.com/v1"), llm.split("/", 1)[1]
    return openai.OpenAI(), llm


_PURE_OPEN_SYS = (
    "Extract a knowledge graph from the text. Return STRICT JSON:\n"
    '{"nodes":[{"id":"<slug>","type":"<EntityType>","properties":{"name":"..."}}],'
    '"relationships":[{"source":"<id>","target":"<id>","type":"<REL_TYPE>"}]}\n'
    "Name entity types and relation types freely as you see fit. IDs must be stable slugs."
)


def _bootstrap_sys(running_vocab: Dict[str, set]) -> str:
    frame = render_upper_frame()
    vocab = ""
    if running_vocab:
        seen = "; ".join(f"{u}: {', '.join(sorted(ts))}" for u, ts in sorted(running_vocab.items()) if ts)
        vocab = ("\n\nSpecific types seen so far (REUSE when they fit; introduce new "
                 f"ones freely otherwise):\n  {seen}\n")
    return (
        frame + vocab +
        '\n\nReturn STRICT JSON: {"nodes":[{"id":"<slug>","type":"<SpecificType>",'
        '"upper":"<FoundationalCategory>","properties":{"name":"..."}}],'
        '"relationships":[{"source":"<id>","target":"<id>","type":"<REL_TYPE>",'
        '"upper":"<abstractRelation>"}]}\nIDs must be stable slugs.'
    )


def _extract(client, model, system, text) -> Dict[str, Any]:
    try:
        r = client.chat.completions.create(
            model=model, temperature=0.0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
        )
        raw = r.choices[0].message.content or "{}"
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        d = json.loads(raw)
        return {"nodes": d.get("nodes", []) or [], "relationships": d.get("relationships", []) or []}
    except Exception as e:
        print(f"    extract error: {type(e).__name__} {str(e)[:100]}")
        return {"nodes": [], "relationships": []}


def _normalize_type(t: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", str(t).lower())
    return s[:-1] if s.endswith("s") else s


def _measure(graph: Dict[str, Any], arm: str) -> Dict[str, Any]:
    nodes, rels = graph["nodes"], graph["relationships"]
    raw_types = [str(n.get("label", n.get("type", ""))) for n in nodes if n.get("label", n.get("type"))]
    distinct_raw = sorted(set(raw_types))
    distinct_norm = sorted({_normalize_type(t) for t in raw_types})
    names = {str((n.get("properties", {}) or {}).get("name", n.get("id", ""))).lower() for n in nodes}
    companies = sorted({c for c in _EXPECTED_COMPANIES if any(c in nm for nm in names)})
    onto, axioms = induce_ontology_from_graph(graph)
    hierarchical = sum(1 for nd in onto.nodes.values() if nd.broader)
    m = {
        "nodes": len(nodes),
        "relationships": len(rels),
        "distinct_entity_types": len(distinct_raw),
        "distinct_normalized_types": len(distinct_norm),
        "type_drift_spread": round(len(distinct_raw) / max(len(distinct_norm), 1), 3),
        "distinct_entity_names": len(names),
        "company_coverage": f"{len(companies)}/{len(_EXPECTED_COMPANIES)}",
        "induced_types": len(onto.nodes),
        "induced_hierarchical_types": hierarchical,
        "induced_relationships": len(onto.relationships),
        "axioms_mined": len(axioms),
        "entity_types_sample": distinct_raw[:20],
    }
    if arm == "BOOTSTRAP":
        rep = induction_report(graph)
        m["anchor_rate"] = rep["anchor_rate"]
        m["upper_categories_used"] = rep["upper_categories_used"]
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--llm", default="mara/MiniMax-M2.7")
    ap.add_argument("--smoke", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    _load_env()
    client, model = _client(args.llm)

    docs = json.load(open(_ROOT / _CORPUS))
    if args.smoke:
        docs = docs[: args.smoke]

    arms: Dict[str, Dict[str, Any]] = {}
    # PURE_OPEN
    print(f"=== PURE_OPEN ({len(docs)} docs) ===")
    g_open = {"nodes": [], "relationships": []}
    for i, item in enumerate(docs):
        r = _extract(client, model, _PURE_OPEN_SYS, item["text"])
        for n in r["nodes"]:
            n["label"] = n.get("type", n.get("label", ""))
        g_open["nodes"] += r["nodes"]
        g_open["relationships"] += r["relationships"]
        print(f"  [{i}] +{len(r['nodes'])}n +{len(r['relationships'])}r")
    arms["PURE_OPEN"] = _measure(g_open, "PURE_OPEN")

    # BOOTSTRAP (upper-anchored + running vocabulary)
    print(f"=== BOOTSTRAP ({len(docs)} docs) ===")
    g_boot = {"nodes": [], "relationships": []}
    running: Dict[str, set] = {}
    for i, item in enumerate(docs):
        r = _extract(client, model, _bootstrap_sys(running), item["text"])
        for n in r["nodes"]:
            n["label"] = n.get("type", n.get("label", ""))
            up = n.get("upper", "")
            if up:
                n.setdefault("properties", {})["upper"] = up
                running.setdefault(str(up), set()).add(str(n.get("type", "")))
        g_boot["nodes"] += r["nodes"]
        g_boot["relationships"] += r["relationships"]
        print(f"  [{i}] +{len(r['nodes'])}n +{len(r['relationships'])}r | vocab={sum(len(v) for v in running.values())} types")
    arms["BOOTSTRAP"] = _measure(g_boot, "BOOTSTRAP")

    report = {"corpus": _CORPUS, "docs": len(docs), "model": args.llm, "arms": arms}
    print("\n=== cold-start extraction A/B (seocho-ia4.11) ===")
    hdr = ("metric", "PURE_OPEN", "BOOTSTRAP")
    print(f"  {hdr[0]:28s} {hdr[1]:>12s} {hdr[2]:>12s}")
    for k in ["nodes", "relationships", "distinct_entity_types", "type_drift_spread",
              "company_coverage", "induced_types", "induced_hierarchical_types",
              "induced_relationships", "axioms_mined"]:
        a, b = arms["PURE_OPEN"].get(k, "-"), arms["BOOTSTRAP"].get(k, "-")
        print(f"  {k:28s} {str(a):>12s} {str(b):>12s}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
