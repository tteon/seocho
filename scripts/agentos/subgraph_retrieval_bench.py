"""End-to-end subgraph retrieval: intern-table resolution vs fuzzy-vector RAG.

Closes boundary 1 of the paper spine (obsidian wiki/paper-spine-string-to-subgraph.md)
at READ time and measures its downstream effect on the retrieved subgraph.

A user request is text. Answering it means: resolve the surface mention to the
ONE canonical entity it denotes (boundary 1) → expand that anchor's typed
neighborhood (boundary 2) → return the subgraph (boundary 3). The claim: the
write-time intern table (``seocho.index.identity.compute_node_identity``) is the
correct read-time resolver, and generic vector-RAG structurally cannot match it
because content-equal mentions embed apart while homonyms embed together.

Ground truth (FinBench, deterministic): a Company/Person entity's subgraph = the
set of Accounts it owns (``Account.owner_id == entity.id``). Anchor resolution is
therefore causal: a wrong anchor returns a wholly wrong owned-account set, so
``wrong_anchor_rate`` is the primary metric and subgraph precision/recall is its
downstream consequence — exactly the spine's "wrong anchor → wrong subgraph".

Arms (with ceiling+floor controls, per the ontology-experiment discipline —
report the effect against the null spread, seocho-02t lesson):
  * ``floor_random``   — anchor = deterministic pseudo-random entity (null floor)
  * ``vector_name``    — bge NN over entity NAME only (naive graph-RAG practice)
  * ``vector_disamb``  — bge NN over "name | disambiguator" (strong vector baseline)
  * ``intern``         — compute_node_identity composite-key lookup (the method)
  * ``ceiling_oracle`` — anchor = the true entity always (upper bound)

Conditions crossed: {normalizable | suffix} surface variation × {unique | homonym}.

Usage:
  python scripts/agentos/subgraph_retrieval_bench.py \
      --nodes-dir outputs/finbench/sf1/nodes --label Company \
      --homonym-rate 0.3 --max-targets 150 \
      --out outputs/agentos/subgraph_retrieval_company_sf1.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from seocho.index.identity import compute_node_identity  # noqa: E402
from seocho.store.fastembed_backend import make_fastembed_backend  # noqa: E402

_SUFFIXES = [" Inc", " Co.", " Ltd", ", LLC"]
_DISAMB = {"Company": "sector", "Person": "country"}


def _normalizable(name: str) -> List[Tuple[str, str]]:
    return [("canonical", name), ("case", name.upper()), ("whitespace", f"  {name}  ")]


def _suffix(name: str, k: int = 2) -> List[Tuple[str, str]]:
    return [("suffix", name + _SUFFIXES[j % len(_SUFFIXES)]) for j in range(k)]


def load(nodes_dir: Path, label: str) -> Tuple[List[Dict[str, Any]], Dict[int, List[int]]]:
    import duckdb

    con = duckdb.connect()
    disamb = _DISAMB[label]
    rows = con.execute(
        f"select id, name, {disamb} from '{nodes_dir}/{label}.parquet' order by id"
    ).fetchall()
    owned: Dict[int, List[int]] = {}
    for acct_id, owner in con.execute(
        f"select id, owner_id from '{nodes_dir}/Account.parquet'"
    ).fetchall():
        owned.setdefault(int(owner), []).append(int(acct_id))
    entities = [
        {"id": int(r[0]), "name": str(r[1]), "disamb_key": disamb,
         "disamb_val": str(r[2]), "accounts": sorted(owned.get(int(r[0]), []))}
        for r in rows if owned.get(int(r[0]))          # non-empty subgraph only
    ]
    return entities, owned


def plant_homonyms(entities: List[Dict[str, Any]], rate: float) -> set:
    """Give paired distinct entities the same surface name (differ in disamb).
    Returns the set of entity ids that are homonyms."""
    n = len(entities)
    half = n // 2
    homonym_ids = set()
    for i in range(int(half * rate)):
        a, b = entities[i], entities[i + half]
        if a["disamb_val"] == b["disamb_val"]:
            continue                                   # unseparable by any key — skip
        b["name"] = a["name"]                          # shared surface, distinct disamb
        homonym_ids.update({a["id"], b["id"]})
    return homonym_ids


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class Resolver:
    def __init__(self, entities: List[Dict[str, Any]], label: str, embed) -> None:
        self.entities = entities
        self.label = label
        self.by_id = {e["id"]: e for e in entities}
        # intern index: composite address -> entity id
        keys = ["name", entities[0]["disamb_key"]]
        self.intern_index: Dict[str, int] = {}
        for e in entities:
            addr = compute_node_identity(
                label, {"name": e["name"], e["disamb_key"]: e["disamb_val"]}, keys)
            if addr is not None:
                self.intern_index.setdefault(addr, e["id"])
        self.keys = keys
        # vector indexes (name-only and name|disamb)
        self._name_vecs = embed([e["name"] for e in entities])
        self._disamb_vecs = embed(
            [f"{e['name']} | {e['disamb_val']}" for e in entities])
        self._ids = [e["id"] for e in entities]

    def intern(self, mention: str, disamb_val: str) -> Optional[int]:
        addr = compute_node_identity(
            self.label, {"name": mention, self.keys[1]: disamb_val}, self.keys)
        return self.intern_index.get(addr) if addr else None

    def _nn(self, qvec: List[float], mat: List[List[float]]) -> int:
        best_i, best_s = 0, -2.0
        for i, v in enumerate(mat):
            s = _cosine(qvec, v)
            if s > best_s:
                best_i, best_s = i, s
        return self._ids[best_i]

    def vector_name(self, mention: str, embed) -> int:
        return self._nn(embed([mention])[0], self._name_vecs)

    def vector_disamb(self, mention: str, disamb_val: str, embed) -> int:
        return self._nn(embed([f"{mention} | {disamb_val}"])[0], self._disamb_vecs)


def _prf(retrieved: List[int], truth: List[int]) -> Tuple[float, float]:
    if not retrieved:
        return 0.0, 0.0
    rset, tset = set(retrieved), set(truth)
    inter = len(rset & tset)
    precision = inter / len(rset)
    recall = inter / len(tset) if tset else 0.0
    return precision, recall


def run(entities, resolver, embed, homonym_ids, *, suffix_k) -> Dict[str, Any]:
    arms = ["floor_random", "vector_name", "vector_disamb", "intern", "ceiling_oracle"]
    # accumulators: arm -> condition -> {wrong, prec, rec, n}
    acc: Dict[str, Dict[str, Dict[str, float]]] = {
        a: {} for a in arms}

    def bump(arm, cond, wrong, p, r):
        d = acc[arm].setdefault(cond, {"wrong": 0.0, "prec": 0.0, "rec": 0.0, "n": 0})
        d["wrong"] += wrong
        d["prec"] += p
        d["rec"] += r
        d["n"] += 1

    for idx, e in enumerate(entities):
        true_accts = e["accounts"]
        homonym = e["id"] in homonym_ids
        h_tag = "homonym" if homonym else "unique"
        variants = _normalizable(e["name"]) + _suffix(e["name"], suffix_k)
        for fam, mention in variants:
            fam_tag = "suffix" if fam == "suffix" else "normalizable"
            cond = f"{fam_tag}/{h_tag}"
            # resolve per arm -> anchor id
            anchors = {
                "floor_random": entities[(idx * 2654435761) % len(entities)]["id"],
                "vector_name": resolver.vector_name(mention, embed),
                "vector_disamb": resolver.vector_disamb(mention, e["disamb_val"], embed),
                "intern": resolver.intern(mention, e["disamb_val"]),
                "ceiling_oracle": e["id"],
            }
            for arm, anchor in anchors.items():
                if anchor is None:                      # intern miss = no subgraph
                    bump(arm, cond, 1.0, 0.0, 0.0)
                    continue
                retrieved = resolver.by_id[anchor]["accounts"]
                p, r = _prf(retrieved, true_accts)
                bump(arm, cond, 0.0 if anchor == e["id"] else 1.0, p, r)

    # finalize means + overall
    report = {"arms": {}}
    for arm in arms:
        conds = {}
        tot = {"wrong": 0.0, "prec": 0.0, "rec": 0.0, "n": 0}
        for cond, d in sorted(acc[arm].items()):
            conds[cond] = {
                "wrong_anchor_rate": round(d["wrong"] / d["n"], 4),
                "subgraph_precision": round(d["prec"] / d["n"], 4),
                "subgraph_recall": round(d["rec"] / d["n"], 4),
                "n": int(d["n"]),
            }
            for k in ("wrong", "prec", "rec", "n"):
                tot[k] += d[k]
        conds["ALL"] = {
            "wrong_anchor_rate": round(tot["wrong"] / tot["n"], 4),
            "subgraph_precision": round(tot["prec"] / tot["n"], 4),
            "subgraph_recall": round(tot["rec"] / tot["n"], 4),
            "n": int(tot["n"]),
        }
        report["arms"][arm] = conds
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--nodes-dir", required=True)
    ap.add_argument("--label", default="Company", choices=list(_DISAMB))
    ap.add_argument("--homonym-rate", type=float, default=0.3)
    ap.add_argument("--suffix-k", type=int, default=2)
    ap.add_argument("--max-targets", type=int, default=150)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    backend = make_fastembed_backend()
    if backend is None:
        print("fastembed unavailable — cannot run the vector baseline", file=sys.stderr)
        sys.exit(2)
    embed = backend.embed

    entities, _ = load(Path(args.nodes_dir), args.label)
    entities = entities[: args.max_targets]
    homonym_ids = plant_homonyms(entities, args.homonym_rate)
    print(f"{args.label}: {len(entities)} targets (with owned accounts), "
          f"{len(homonym_ids)} homonym entities, embedding with bge...", flush=True)
    resolver = Resolver(entities, args.label, embed)
    report = run(entities, resolver, embed, homonym_ids, suffix_k=args.suffix_k)
    report["meta"] = {"label": args.label, "targets": len(entities),
                      "homonym_entities": len(homonym_ids),
                      "nodes_dir": args.nodes_dir}

    print(f"\n=== {args.label} subgraph retrieval (wrong-anchor / P / R) ===")
    for arm, conds in report["arms"].items():
        a = conds["ALL"]
        print(f"  {arm:15s} ALL  wrong={a['wrong_anchor_rate']:.2%} "
              f"P={a['subgraph_precision']:.3f} R={a['subgraph_recall']:.3f} "
              f"(n={a['n']})")
    print("\n  by condition (wrong-anchor rate):")
    conds_all = sorted({c for arm in report["arms"].values() for c in arm} - {"ALL"})
    header = "    " + " ".join(f"{c:22s}" for c in conds_all)
    print(header)
    for arm in report["arms"]:
        row = "    " + " ".join(
            f"{report['arms'][arm].get(c, {}).get('wrong_anchor_rate', float('nan')):<22.2%}"
            for c in conds_all)
        print(f"  {arm:15s}\n{row}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
