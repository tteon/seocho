#!/usr/bin/env python3
"""Write measured degree facts into the ontology, so the prompt can mention them.

Directional roles established the general argument: a prompt can only convey what the
schema holds, and the improvement came from the ontology gaining a vocabulary it lacked
rather than from better prompt wording. The same gap remains one level along. Nothing in a
schema of labels, property names and endpoint types says that one account carries 158,315
transfer edges while the median carries six — so a model has no basis for preferring a
bounded shape, and the measured consequence is not subtle: an aggregate anchored on a hub
does not return at all, where the same question on a median node costs 45 ms.

This closes that gap the honest way, by *deriving* the fact from the data instead of
asserting it. Values come from the snapshot the graph was loaded from, which is the role
FinBench's factor tables play. An ontology stating a distribution it never measured is
worse than one that stays silent, so the annotation records the source it was computed
from and refuses to invent anything for relationships it cannot measure.

Only ``transfer`` is measured today because it is the only edge table in this dataset with
a non-trivial degree distribution; the others are near-functional (one OWN per account,
one DEPOSIT per loan) and a hint there would be noise dressed as information.

Usage:
    python scripts/finbench/annotate_ontology_degrees.py \
        --src outputs/finbench/sf1000-real \
        --ontology examples/finbench/finbench.ontology.yaml
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

# Above this ratio the distribution is worth warning a planner about. Chosen from the
# measured contrast rather than taste: the uniform graph sits at max/median ~3 and shows
# no hub effect at all, while the power-law graph at ~26,000 times out on aggregates.
HEAVY_TAIL_RATIO = 50.0

# Relationship -> (edge table, source column). Deliberately explicit: silence is the
# correct output for an edge whose degree carries no information.
MEASURABLE = {"TRANSFER": ("transfer", "src", "dst")}


def measure_degrees(src: Path, table: str, src_col: str, dst_col: str) -> Dict[str, Any]:
    import duckdb

    con = duckdb.connect()
    con.execute("SET memory_limit='8GB'")
    path = str(src / "edges" / f"{table}.parquet")
    out = con.execute(
        f"""
        WITH d AS (SELECT {src_col} AS v, count(*) AS deg FROM '{path}' GROUP BY {src_col})
        SELECT quantile_disc(deg, 0.5), quantile_disc(deg, 0.99), max(deg), avg(deg)
        FROM d
        """
    ).fetchone()
    inc = con.execute(
        f"""
        WITH d AS (SELECT {dst_col} AS v, count(*) AS deg FROM '{path}' GROUP BY {dst_col})
        SELECT quantile_disc(deg, 0.5), max(deg) FROM d
        """
    ).fetchone()
    median_out, p99_out = int(out[0]), int(out[1])
    max_out, mean_out = int(out[2]), float(out[3])
    ratio = max_out / median_out if median_out else float("inf")
    return {
        "median_out": median_out,
        "p99_out": p99_out,
        "max_out": max_out,
        "mean_out": round(mean_out, 2),
        "median_in": int(inc[0]),
        "max_in": int(inc[1]),
        "max_over_median_out": round(ratio, 1),
        "heavy_tailed": ratio >= HEAVY_TAIL_RATIO,
        # Recorded so a reader can tell a measured hint from an asserted one, and can see
        # which snapshot it describes — a hint carried over from a differently-shaped
        # dataset would be actively misleading.
        "measured_from": str(src),
    }


def _splice(text: str, rtype: str, hint: Dict[str, Any]) -> str:
    """Replace or insert one relationship's degreeHint, leaving the rest of the file alone.

    A full ``yaml.safe_dump`` round-trip is the obvious implementation and the wrong one:
    it silently discards every comment in the file, and this ontology carries hand-written
    rationale for *why* its roles and hints exist. A tool whose job is to add one fact
    should not delete the documentation around it.
    """
    lines = text.splitlines()
    body = ["    degreeHint:"] + [
        f"      {k}: {json.dumps(v) if isinstance(v, str) else repr(v).lower() if isinstance(v, bool) else v}"
        for k, v in hint.items()
    ]

    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"{rtype}:"), None)
    if start is None:
        raise SystemExit(f"{rtype} not found in the ontology")
    # The relationship block runs until the next key at the same indentation.
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = len(lines)
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            end = i
            break
    block = lines[start:end]

    existing = next((i for i, ln in enumerate(block) if ln.strip() == "degreeHint:"), None)
    if existing is not None:
        stop = len(block)
        for i in range(existing + 1, len(block)):
            ln = block[i]
            if ln.strip() and (len(ln) - len(ln.lstrip())) <= 4:
                stop = i
                break
        block = block[:existing] + body + block[stop:]
    else:
        block = block + body
    return "\n".join(lines[:start] + block + lines[end:]) + "\n"


def main() -> None:
    import yaml

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--ontology", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="defaults to updating --ontology in place")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    text = args.ontology.read_text()
    doc = yaml.safe_load(text)
    rels = doc.get("relationships") or {}
    changed = {}
    for rtype, (table, src_col, dst_col) in MEASURABLE.items():
        if rtype not in rels:
            continue
        if not (args.src / "edges" / f"{table}.parquet").exists():
            print(f"[degrees] {rtype}: no {table}.parquet under {args.src}, skipping")
            continue
        hint = measure_degrees(args.src, table, src_col, dst_col)
        text = _splice(text, rtype, hint)
        changed[rtype] = hint
        print(f"[degrees] {rtype}: median_out={hint['median_out']} "
              f"p99_out={hint['p99_out']} max_out={hint['max_out']:,} "
              f"ratio={hint['max_over_median_out']:,} "
              f"heavy_tailed={hint['heavy_tailed']}")

    if not changed:
        raise SystemExit("nothing measurable was annotated")
    if args.dry_run:
        print(json.dumps(changed, indent=2))
        return
    target = args.out or args.ontology
    target.write_text(text)
    # Re-parse so a malformed splice fails here rather than at query time.
    reparsed = yaml.safe_load(target.read_text())
    for rtype, hint in changed.items():
        got = (reparsed.get("relationships") or {}).get(rtype, {}).get("degreeHint")
        if got != hint:
            raise SystemExit(f"splice produced {got!r}, expected {hint!r}")
    print(f"[degrees] wrote {target} (comments preserved, re-parse verified)")


if __name__ == "__main__":
    main()
