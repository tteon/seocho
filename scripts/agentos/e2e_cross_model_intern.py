"""Cross-model shared intern table: do different models share one canonical namespace?

hadry's OS test: give the SAME ontology to DIFFERENT models and check whether they
populate the SAME canonical addresses in ONE shared intern table. If yes, the ontology
is a genuine shared type system + address space across models — that is what an OS
memory manager IS: many clients (here, models/agents), one governed heap. Cross-session
too: the table persists to a shared file, so each model run accumulates into the same
namespace across processes.

Setup: finance-compliance ontology (identity_keys patched to ["name"] on the named
types), FinDER subset (10 docs, real companies), one SharedInternTable persisted across
runs. Each model extracts the SAME corpus with the SAME ontology-typed prompt; entities
are interned into the shared table (identity = label|normalized-name).

Metrics:
- cross-model AGREEMENT: of all distinct canonical entities, how many were produced by
  >=2 models / all 3 (the shared-namespace overlap).
- COLLAPSE: shared-table hits = cross-model+cross-doc duplicate entities folded to one
  address (the allocator's collapse metric, now across models).
- per-model contribution + entities unique to one model (divergence — the honest limit:
  name variants / off-ontology types don't converge).

Usage: python scripts/agentos/e2e_cross_model_intern.py \
    --models MiniMax-M2.7,gpt-oss-120b,gemma-4-31B-it \
    --out outputs/agentos/cross_model_intern.json [--smoke N]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any, Dict

import sys
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from seocho.index.identity import apply_identity_keys  # noqa: E402
from seocho.index.shared_intern import SharedInternTable  # noqa: E402

_CORPUS = "examples/finder/datasets/finder_tutorial_subset.json"
_INTERN_FILE = _ROOT / "outputs/agentos/cross_model_intern_namespace.json"


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
    onto = mod.build_ontology()
    # patch identity_keys=["name"] on every node type that has a unique name, so
    # apply_identity_keys interns entities by their canonical name.
    for nd in onto.nodes.values():
        if "name" in (nd.properties or {}) and not getattr(nd, "identity_keys", None):
            nd.identity_keys = ["name"]
    return onto


def _client():
    import openai
    return openai.OpenAI(api_key=os.environ["MARA_API_KEY"],
                         base_url="https://api.cloud.mara.com/v1")


def _sys_prompt(onto) -> str:
    types = ", ".join(sorted(onto.nodes.keys()))
    rels = ", ".join(sorted(onto.relationships.keys()))
    return (
        "Extract a knowledge graph using ONLY these entity types and relation types.\n"
        f"Entity types: {types}\n"
        f"Relation types: {rels}\n"
        'Return STRICT JSON: {"nodes":[{"id":"<slug>","label":"<EntityType>",'
        '"properties":{"name":"<canonical name>"}}],'
        '"relationships":[{"source":"<id>","target":"<id>","type":"<RELATION>"}]}\n'
        "Use the entity's canonical name (e.g. 'Microsoft', not 'the company'). "
        "Pick the single best-fitting listed type for each entity."
    )


def _extract(client, model, system, text) -> Dict[str, Any]:
    try:
        r = client.chat.completions.create(
            model=model, temperature=0.0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}])
        raw = re.sub(r"^```(json)?|```$", "", (r.choices[0].message.content or "{}").strip(),
                     flags=re.MULTILINE).strip()
        d = json.loads(raw)
        return {"nodes": d.get("nodes", []) or [], "relationships": d.get("relationships", []) or []}
    except Exception as e:
        print(f"    {model} extract error: {type(e).__name__} {str(e)[:90]}")
        return {"nodes": [], "relationships": []}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", default="MiniMax-M2.7,gpt-oss-120b,gemma-4-31B-it")
    ap.add_argument("--smoke", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    _load_env()
    onto = _ontology()
    client = _client()
    system = _sys_prompt(onto)
    docs = json.load(open(_ROOT / _CORPUS))
    if args.smoke:
        docs = docs[: args.smoke]
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    table = SharedInternTable()          # ONE shared canonical namespace
    table.load(_INTERN_FILE)             # cross-session: accumulate into prior runs
    per_model: Dict[str, set] = {}

    for model in models:
        print(f"=== {model} ({len(docs)} docs) ===")
        ids: set = set()
        for i, item in enumerate(docs):
            r = _extract(client, model, system, item["text"])
            nodes = [n for n in r["nodes"] if isinstance(n, dict)]
            # intern into the SHARED table (workspace = the shared corpus namespace)
            apply_identity_keys(onto, nodes, r["relationships"],
                                intern_table=table, workspace_id="finder")
            for n in nodes:
                nid = str(n.get("id", ""))
                if "|" in nid:            # a canonical composite id (interned)
                    ids.add(nid)
            print(f"  [{i}] +{len(nodes)}n | canonical so far={len(ids)}")
        per_model[model] = ids

    table.persist(_INTERN_FILE)          # cross-session: save the namespace

    # cross-model agreement over canonical entities
    all_ids = set().union(*per_model.values()) if per_model else set()
    def count_models(cid):
        return sum(1 for s in per_model.values() if cid in s)
    by_all = sorted(c for c in all_ids if count_models(c) == len(models))
    by_ge2 = sorted(c for c in all_ids if count_models(c) >= 2)
    unique1 = sorted(c for c in all_ids if count_models(c) == 1)

    report = {
        "models": models, "docs": len(docs), "ontology": "finance-compliance",
        "shared_namespace_size": len(all_ids),
        "agreed_by_all_models": len(by_all),
        "agreed_by_ge2_models": len(by_ge2),
        "unique_to_one_model": len(unique1),
        "agreement_rate_ge2": round(len(by_ge2) / max(len(all_ids), 1), 3),
        "agreement_rate_all": round(len(by_all) / max(len(all_ids), 1), 3),
        "intern_table_stats": table.stats(),
        "per_model_canonical_count": {m: len(s) for m, s in per_model.items()},
        "sample_agreed_by_all": by_all[:15],
        "sample_unique_to_one": unique1[:15],
    }

    print("\n=== cross-model shared intern table (seocho-ia4) ===")
    print(f"  models: {models}")
    print(f"  shared canonical namespace: {len(all_ids)} entities")
    print(f"  agreed by ALL {len(models)} models: {len(by_all)} ({report['agreement_rate_all']:.0%})")
    print(f"  agreed by >=2 models:          {len(by_ge2)} ({report['agreement_rate_ge2']:.0%})")
    print(f"  unique to one model:           {len(unique1)}")
    print(f"  interning collapse (table hits): {table.stats()['hits']} "
          f"(cross-model+cross-doc duplicates folded)")
    print(f"  per-model canonical entities: {report['per_model_canonical_count']}")
    print(f"  sample agreed-by-all: {by_all[:8]}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
