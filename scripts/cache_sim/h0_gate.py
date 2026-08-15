"""WP0 gate verdict: H0 (working-set overlap) and H1 (node-appearance skew).

Operationalization, stated up front because it IS the result's fine print:

- DB-side working set = nodes bound by each replayed call's MATCH..WHERE
  (shadow elementId queries). This is a *lower bound* on the page working
  set — DozerDB CE exposes no page identities (measured 2026-08-15), and
  index pages / scanned-but-unbound entities are invisible. A lower bound
  biases H0 *upward* (missing DB-only nodes would only shrink overlap), so
  a FAIL verdict is robust; a PASS near the threshold is not.
- KV-side working set = nodes whose identity survives into the serialized
  context (RETURN-exposed variables outside aggregates, plus nothing else:
  a count() row carries no node into the LLM's cache).
- H0 metric: Jaccard of the two universes, plus Jaccard of the top-decile
  (by episode-appearance frequency) hot sets. Threshold (set here, absent
  from the plan): top-decile Jaccard >= 0.30 — below that the hot sets are
  mostly disjoint and there is no shared working set to co-manage.
- H1 metric: share of context-node appearances captured by the top 10% of
  context nodes. Plan's own gate: < 30% -> reject pin/quantization.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def load_sets(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def hot_set(counter: Counter, decile: float = 0.10) -> set:
    if not counter:
        return set()
    k = max(1, int(len(counter) * decile))
    return {node for node, _ in counter.most_common(k)}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def analyze(records, sf: int) -> dict:
    rows = [r for r in records if r["sf"] == sf]
    read_freq: Counter = Counter()
    ctx_freq: Counter = Counter()
    covered = 0
    for r in rows:
        if r["read_ids"]:
            covered += 1
        read_freq.update(set(r["read_ids"]))
        ctx_freq.update(set(r["ctx_ids"]))

    read_universe, ctx_universe = set(read_freq), set(ctx_freq)
    top_read, top_ctx = hot_set(read_freq), hot_set(ctx_freq)

    total_appearances = sum(ctx_freq.values())
    top_ctx_appearances = sum(ctx_freq[n] for n in top_ctx)
    h1_share = top_ctx_appearances / total_appearances if total_appearances else 0.0

    return {
        "sf": sf,
        "episodes": len(rows),
        "episodes_with_read_set": covered,
        "db_universe": len(read_universe),
        "kv_universe": len(ctx_universe),
        "kv_in_db_containment": (len(ctx_universe & read_universe) / len(ctx_universe)
                                 if ctx_universe else 0.0),
        "h0_jaccard_full": round(jaccard(read_universe, ctx_universe), 4),
        "h0_jaccard_top_decile": round(jaccard(top_read, top_ctx), 4),
        "h1_top_decile_appearance_share": round(h1_share, 4),
        "db_top10": read_freq.most_common(10),
        "kv_top10": ctx_freq.most_common(10),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sets", nargs="+", required=True)
    parser.add_argument("--h0-threshold", type=float, default=0.30)
    parser.add_argument("--h1-threshold", type=float, default=0.30)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    records = []
    for path in args.sets:
        records.extend(load_sets(Path(path)))
    sfs = sorted({r["sf"] for r in records})

    report = {"operationalization": __doc__.strip(),
              "h0_threshold_top_decile_jaccard": args.h0_threshold,
              "h1_threshold_top_decile_share": args.h1_threshold,
              "per_sf": [analyze(records, sf) for sf in sfs]}

    verdicts = {}
    for row in report["per_sf"]:
        verdicts[f"sf{row['sf']}"] = {
            "H0": "PASS" if row["h0_jaccard_top_decile"] >= args.h0_threshold else "FAIL",
            "H1": "PASS" if row["h1_top_decile_appearance_share"] >= args.h1_threshold else "FAIL",
        }
    report["verdicts"] = verdicts

    rendered = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(rendered)
    for row in report["per_sf"]:
        print(f"SF{row['sf']}: episodes={row['episodes']} (read-set coverage "
              f"{row['episodes_with_read_set']}/{row['episodes']}) "
              f"db_universe={row['db_universe']} kv_universe={row['kv_universe']}")
        print(f"  H0 full-Jaccard={row['h0_jaccard_full']} "
              f"top-decile-Jaccard={row['h0_jaccard_top_decile']} "
              f"containment(KV⊆DB)={row['kv_in_db_containment']:.3f}")
        print(f"  H1 top-decile appearance share={row['h1_top_decile_appearance_share']}")
    print("verdicts:", json.dumps(verdicts))


if __name__ == "__main__":
    main()
