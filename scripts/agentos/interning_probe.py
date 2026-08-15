"""Interning probe: measure SEOCHO's identity-key table as a memory allocator.

The memory-allocator reframe (hadry 2026-08-15,
obsidian wiki/memory-allocator-model-design.md): SEOCHO's ``identity_keys`` ->
composite-id MERGE is hash-consing / string interning, and the UNIQUE
constraint on that id is the intern table (the "ontology hash -> hashtable").
Its *defining* metric is not latency but the two properties every interning
allocator lives or dies by:

  * COLLAPSE — do multiple mentions of the SAME canonical entity (surface
    variation across documents / agents) resolve to ONE address? An allocator
    that gives the same entity two addresses has failed to intern.
  * COLLISION — do DISTINCT canonical entities that happen to share a member
    (homonyms: two "J. Smith", PTC's vs Tesla's "Total revenue") wrongly land
    on ONE address? An allocator that aliases distinct objects has corrupted
    the heap.

This probe exercises the REAL identity code (``seocho.index.identity.
compute_node_identity``, the function the write path calls) — not a
reimplementation — over a FinBench entity population with:

  * deterministic surface variation injected per canonical (case / whitespace /
    legal-suffix / punctuation — what LLM extraction actually produces), and
  * planted homonym collisions (distinct canonicals sharing a surface name but
    differing in a disambiguating property).

It compares two intern-key policies:

  * ``name_only``  — identity_keys = ["name"]                  (the naive allocator)
  * ``composite``  — identity_keys = ["name", <disambiguator>] (seocho-uxs)

and sweeps population scale. No DB, no LLM: the identity function is pure, so
the measurement is byte-deterministic and isolates the allocator's key policy
from storage quirks.

Usage:
  python scripts/agentos/interning_probe.py \
      --nodes-dir outputs/finbench/sf1/nodes \
      --variants 4 --homonym-rate 0.15 \
      --out outputs/agentos/interning_sf1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from seocho.index.identity import compute_node_identity  # noqa: E402


# ---------------------------------------------------------------------------
# Deterministic surface variation — the forms one canonical entity takes across
# documents/agents. No randomness: variant i is a fixed transform, so the whole
# probe is reproducible. `_normalize_segment` (case/whitespace) is expected to
# collapse variants 1-2; the legal-suffix/punctuation variants are NOT
# normalized away and expose the intern table's real recall ceiling — reported,
# not hidden.
# ---------------------------------------------------------------------------

_SUFFIXES = [" Inc", " Co.", " Ltd", ", LLC"]

# Two variant families with different expectations, kept separate so collapse
# is measured honestly rather than as one blunt rate:
#   NORMALIZABLE — case + leading/trailing whitespace. `_normalize_segment`
#     (lower + strip + collapse-spaces) SHOULD fold these to the canonical.
#   SUFFIX — appended legal suffix. Semantically the same entity, but NOT
#     normalized away — this is the intern table's real recall ceiling and the
#     motivation for alias / same_as handling.


def normalizable_variants(name: str) -> List[str]:
    return [name, name.upper(), f"  {name}  "]


def suffix_variants(name: str, k: int) -> List[str]:
    return [name + _SUFFIXES[j % len(_SUFFIXES)] for j in range(k)]


def load_population(nodes_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    import duckdb

    con = duckdb.connect()
    pop: Dict[str, List[Dict[str, Any]]] = {}
    specs = {
        "Person": ("name", "country"),
        "Company": ("name", "sector"),
    }
    for label, (_name_col, disamb) in specs.items():
        path = nodes_dir / f"{label}.parquet"
        if not path.exists():
            continue
        rows = con.execute(
            f"select id, name, {disamb} from '{path}' order by id").fetchall()
        pop[label] = [
            {"canonical_id": f"{label}:{r[0]}", "name": str(r[1]),
             "disamb_key": disamb, "disamb_val": str(r[2])}
            for r in rows]
    return pop


def plant_homonyms(entities: List[Dict[str, Any]], rate: float
                   ) -> List[Tuple[str, str]]:
    """Force distinct canonicals to share a surface name (differ in disambig).
    Deterministic: pair entity i with entity i+half for the first `rate` share.
    Returns the list of planted (canonical_a, canonical_b) collision pairs."""
    n = len(entities)
    half = n // 2
    pairs: List[Tuple[str, str]] = []
    count = int(half * rate)
    for i in range(count):
        a, b = entities[i], entities[i + half]
        if a["disamb_val"] == b["disamb_val"]:
            # a real homonym must differ in the disambiguator, else composite
            # can't separate them either — skip so we measure a fair collision.
            continue
        b["name"] = a["name"]          # same surface name, different disamb
        pairs.append((a["canonical_id"], b["canonical_id"]))
    return pairs


def _id_for(label: str, name: str, ent: Dict[str, Any],
            identity_keys: List[str]) -> str:
    props = {"name": name, ent["disamb_key"]: ent["disamb_val"]}
    node_id = compute_node_identity(label, props, identity_keys)
    return node_id if node_id is not None else f"__raw__:{name}"


def measure(label: str, entities: List[Dict[str, Any]], *, suffix_k: int,
            policy: str, disamb: str, homonym_pairs: List[Tuple[str, str]]
            ) -> Dict[str, Any]:
    identity_keys = ["name"] if policy == "name_only" else ["name", disamb]

    id_to_canon: Dict[str, set] = {}
    canon_to_ids: Dict[str, set] = {}
    norm_folds = 0            # canonicals whose case/whitespace variants all fold
    suffix_mentions = 0
    suffix_folded = 0         # suffix variants that fold onto the canonical id

    for ent in entities:
        canonical_id_str = _id_for(label, ent["name"], ent, identity_keys)
        # NORMALIZABLE family: expect all to fold onto the canonical address.
        norm_ids = {_id_for(label, f, ent, identity_keys)
                    for f in normalizable_variants(ent["name"])}
        if norm_ids == {canonical_id_str}:
            norm_folds += 1
        # SUFFIX family: the recall ceiling.
        for f in suffix_variants(ent["name"], suffix_k):
            suffix_mentions += 1
            if _id_for(label, f, ent, identity_keys) == canonical_id_str:
                suffix_folded += 1
        # Full address accounting over every mention (canonical + all variants).
        all_forms = normalizable_variants(ent["name"]) + \
            suffix_variants(ent["name"], suffix_k)
        for f in all_forms:
            nid = _id_for(label, f, ent, identity_keys)
            id_to_canon.setdefault(nid, set()).add(ent["canonical_id"])
            canon_to_ids.setdefault(ent["canonical_id"], set()).add(nid)

    mean_ids = sum(len(ids) for ids in canon_to_ids.values()) / len(canon_to_ids)
    colliding_ids = {i: c for i, c in id_to_canon.items() if len(c) > 1}
    planted_collided = sum(
        1 for a, b in homonym_pairs
        if canon_to_ids.get(a, set()) & canon_to_ids.get(b, set()))

    return {
        "policy": policy,
        "canonicals": len(canon_to_ids),
        "distinct_addresses": len(id_to_canon),
        # COLLAPSE, split honestly:
        "normalization_collapse_rate": round(norm_folds / len(entities), 4),
        "suffix_recall": round(suffix_folded / suffix_mentions, 4)
        if suffix_mentions else 0.0,
        "mean_addresses_per_canonical": round(mean_ids, 4),
        # COLLISION:
        "colliding_addresses": len(colliding_ids),
        "planted_homonym_pairs": len(homonym_pairs),
        "planted_pairs_collided": planted_collided,
        "collision_rate": round(planted_collided / len(homonym_pairs), 4)
        if homonym_pairs else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--nodes-dir", required=True)
    ap.add_argument("--variants", type=int, default=4)
    ap.add_argument("--homonym-rate", type=float, default=0.15)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    pop = load_population(Path(args.nodes_dir))
    report: Dict[str, Any] = {"nodes_dir": args.nodes_dir,
                              "variants": args.variants,
                              "homonym_rate": args.homonym_rate, "labels": {}}
    for label, entities in pop.items():
        disamb = entities[0]["disamb_key"]
        pairs = plant_homonyms(entities, args.homonym_rate)
        arms = {}
        for policy in ("name_only", "composite"):
            arms[policy] = measure(label, entities, suffix_k=args.variants,
                                   policy=policy, disamb=disamb,
                                   homonym_pairs=pairs)
        report["labels"][label] = {"disambiguator": disamb, "arms": arms}
        print(f"\n== {label} (n={len(entities)}, disamb={disamb}, "
              f"homonym_pairs={len(pairs)}, variants/entity={args.variants}) ==")
        for policy, r in arms.items():
            print(f"  {policy:10s} norm_collapse={r['normalization_collapse_rate']:.2%} "
                  f"suffix_recall={r['suffix_recall']:.2%} "
                  f"mean_addr/canon={r['mean_addresses_per_canonical']:.2f} "
                  f"| collision={r['collision_rate']:.2%} "
                  f"({r['planted_pairs_collided']}/{r['planted_homonym_pairs']}) "
                  f"| addresses={r['distinct_addresses']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
