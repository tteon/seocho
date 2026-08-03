#!/usr/bin/env python3
"""Draw the arithmetic-stratum supplement sample, once, deterministically.

The 280-case sample behind s1/s2 contains zero type!=None cases: the sweep's
preference for multi-reference cases excluded them, because arithmetic
questions almost always carry exactly one reference (881 of 883). That left
the original motivation's central slice — questions the graph was hypothesised
to win — untested. This draw exists to close that hole and nothing else.

Rule, fixed before any extraction under tag s3:
  - pool: every FinDER case with type != "None" and at least one reference
  - size: 140
  - allocation: proportional to each type's pool share, largest-remainder
    rounding; a type whose pool is smaller than its allocation contributes
    every case it has (Subtraction, n=8)
  - within a type: random.Random("42-<type>").sample, same seeding idiom as
    the s1/s2 draw

Writes dataset/arithmetic_supplement_cases.txt (ids, one per line) and prints
the allocation so the numbers land in the preregistration by copy, not memory.
"""
from __future__ import annotations

import importlib.util
import random
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = 140
OUT = ROOT / "dataset/arithmetic_supplement_cases.txt"


def main() -> None:
    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import pandas as pd
    df = pd.read_parquet(module.SOURCE_PARQUET)
    types = dict(zip(df["_id"].astype(str), df["type"].astype(str)))

    pool: dict[str, list[str]] = {}
    for case in module.load_cases_full(seed=42):
        kind = types.get(case["case_id"], "None")
        if kind != "None" and case["references"]:
            pool.setdefault(kind, []).append(case["case_id"])

    total = sum(len(v) for v in pool.values())
    exact = {k: TARGET * len(v) / total for k, v in pool.items()}
    alloc = {k: min(int(exact[k]), len(pool[k])) for k in pool}
    # largest-remainder rounding, capped by each type's pool
    while sum(alloc.values()) < TARGET:
        candidates = [k for k in pool if alloc[k] < len(pool[k])]
        if not candidates:
            break
        best = max(candidates, key=lambda k: exact[k] - alloc[k])
        alloc[best] += 1

    chosen: list[str] = []
    for kind in sorted(pool):
        ids = sorted(pool[kind])
        picker = random.Random(f"42-{kind}")
        chosen += (ids if alloc[kind] >= len(ids)
                   else picker.sample(ids, alloc[kind]))

    OUT.write_text("\n".join(sorted(chosen)) + "\n")
    print(f"pool {total}, drawn {len(chosen)} -> {OUT}")
    for kind in sorted(pool):
        print(f"  {kind:16s} pool {len(pool[kind]):4d}  drawn {alloc[kind]:3d}")
    cats = Counter()
    kept = set(chosen)
    for case in module.load_cases_full(seed=42):
        if case["case_id"] in kept:
            cats[case["category"]] += 1
    print("categories:", dict(cats))


if __name__ == "__main__":
    main()
