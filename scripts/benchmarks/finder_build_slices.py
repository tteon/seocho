#!/usr/bin/env python3
"""Build review-ready FinDER slices from the upstream parquet.

Slices (priority order — first match wins for primary tag):
  S1_FIN_COMP             Financials × Compositional
  S2_FIN_NONQUANT_MULTI   Financials × non-quant × n_refs>=2
  S3_CO_COMP              Company overview × Compositional
  S4_CO_MULTI_NONQUANT    Company overview × non-quant × n_refs>=2
  S5_FN_MULTI             Footnotes × n_refs>=2
  S6_BASELINE_SINGLE      Target-cats × single-ref × non-Compositional (50/cat sample)

Outputs (single consolidated set under .seocho/datasets/finder/slices/):
  all_slices.csv   — every selected record, with `slice` column for filtering
  manifest.json    — programmatic catalog (slice defs, stats, case_id lists)
  SLICES.md        — human-readable catalog
  previews.md      — 3 random query+answer+evidence samples per slice (single file)
"""
from __future__ import annotations

import json
import random
import textwrap
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / ".seocho/datasets/finder/data/train-00000-of-00001.parquet"
OUT = ROOT / ".seocho/datasets/finder/slices"
OUT.mkdir(parents=True, exist_ok=True)

TARGET_CATEGORIES = ["Financials", "Company overview", "Footnotes"]
BASELINE_SAMPLE_PER_CAT = 50
SEED = 42


def slice_predicates(df: pd.DataFrame) -> dict[str, pd.Series]:
    is_comp = df["type"].eq("Compositional")
    is_quant = df["type"].notna() & df["type"].ne("None")
    n_refs = df["references"].apply(len)
    multi = n_refs >= 2
    return {
        "S1_FIN_COMP": (df["category"].eq("Financials") & is_comp),
        "S2_FIN_NONQUANT_MULTI": (df["category"].eq("Financials") & ~is_quant & multi),
        "S3_CO_COMP": (df["category"].eq("Company overview") & is_comp),
        "S4_CO_MULTI_NONQUANT": (df["category"].eq("Company overview") & ~is_quant & multi),
        "S5_FN_MULTI": (df["category"].eq("Footnotes") & multi),
    }


def stratified_baseline(df: pd.DataFrame, exclude_ids: set[str]) -> pd.DataFrame:
    """Sample N single-ref non-Compositional rows per target category as baseline."""
    n_refs = df["references"].apply(len)
    is_comp = df["type"].eq("Compositional")
    pool = df[
        df["category"].isin(TARGET_CATEGORIES)
        & (n_refs == 1)
        & ~is_comp
        & ~df["_id"].isin(exclude_ids)
    ]
    rng = random.Random(SEED)
    picks: list[int] = []
    for cat in TARGET_CATEGORIES:
        cat_pool = pool[pool["category"].eq(cat)]
        ids = list(cat_pool.index)
        rng.shuffle(ids)
        picks.extend(ids[:BASELINE_SAMPLE_PER_CAT])
    return df.loc[picks]


def stats(records: pd.DataFrame) -> dict:
    n_refs = records["references"].apply(len)
    qwords = records["text"].str.split().str.len()
    return {
        "count": int(len(records)),
        "by_category": {k: int(v) for k, v in records["category"].value_counts().items()},
        "by_type": {k: int(v) for k, v in records["type"].fillna("None").value_counts().items()},
        "n_refs": {
            "min": int(n_refs.min()),
            "mean": round(float(n_refs.mean()), 3),
            "median": int(n_refs.median()),
            "max": int(n_refs.max()),
            "distribution": {int(k): int(v) for k, v in n_refs.value_counts().sort_index().items()},
        },
        "query_words": {
            "min": int(qwords.min()),
            "mean": round(float(qwords.mean()), 2),
            "median": int(qwords.median()),
            "max": int(qwords.max()),
        },
        "reasoning_true_count": int(records["reasoning"].sum()),
        "unique_companies_sampled": _sample_companies(records),
    }


def _sample_companies(records: pd.DataFrame) -> list[str]:
    """Pull the first ALL-CAPS 2-5 letter token in each query as a rough ticker proxy."""
    import re

    pat = re.compile(r"\b[A-Z]{2,5}\b")
    seen: list[str] = []
    for q in records["text"]:
        m = pat.search(q)
        if m and m.group() not in seen:
            seen.append(m.group())
        if len(seen) >= 20:
            break
    return seen


REF_SEPARATOR = "\n\n===EVIDENCE_BOUNDARY===\n\n"


def _slice_records_to_df(records: pd.DataFrame, slice_tag: str) -> pd.DataFrame:
    rows = []
    for _, r in records.iterrows():
        refs = list(r["references"])
        rows.append(
            {
                "slice": slice_tag,
                "_id": r["_id"],
                "category": r["category"],
                "type": r["type"] if pd.notna(r["type"]) else "",
                "reasoning": bool(r["reasoning"]),
                "n_refs": len(refs),
                "query_words": len(str(r["text"]).split()),
                "query": r["text"],
                "answer": r["answer"],
                "references_joined": REF_SEPARATOR.join(refs),
            }
        )
    return pd.DataFrame(rows)


def dump_previews(path: Path, slices: dict[str, pd.DataFrame], n: int = 3) -> None:
    """Single consolidated previews file: 3 random samples per slice."""
    lines: list[str] = ["# FinDER slice previews (3 samples per slice)\n"]
    for slice_tag, records in slices.items():
        lines.append(f"\n## {slice_tag} — {len(records)} records\n")
        rng = random.Random(SEED + hash(slice_tag) % 100)
        idx = list(records.index)
        rng.shuffle(idx)
        for i, row_idx in enumerate(idx[:n], 1):
            r = records.loc[row_idx]
            lines.append(
                f"\n### {slice_tag} [{i}] `{r['_id']}` — type={r['type']} | reasoning={r['reasoning']} | n_refs={len(r['references'])}\n"
            )
            lines.append(f"\n**Q:** {r['text']}\n")
            lines.append(
                f"\n**A:** {textwrap.shorten(str(r['answer']), width=600, placeholder=' [...]')}\n"
            )
            for j, ref in enumerate(r["references"], 1):
                clean = ref.replace("\n\n\n", "\n").replace("\n\n", "\n").strip()
                lines.append(
                    f"\n**Evidence[{j}]** ({len(ref)} chars):\n```\n{textwrap.shorten(clean, width=900, placeholder=' [...]')}\n```\n"
                )
            lines.append("\n---\n")
    path.write_text("".join(lines), encoding="utf-8")


def build_manifest(slices: dict[str, pd.DataFrame], df_total: int) -> dict:
    out: dict = {
        "source_parquet": str(SRC.relative_to(ROOT)),
        "source_total_rows": int(df_total),
        "target_categories": TARGET_CATEGORIES,
        "baseline_sample_per_category": BASELINE_SAMPLE_PER_CAT,
        "seed": SEED,
        "slices": {},
    }
    selected_total: set[str] = set()
    for tag, recs in slices.items():
        ids = recs["_id"].tolist()
        selected_total.update(ids)
        out["slices"][tag] = {
            "definition": SLICE_DEFINITIONS[tag],
            "purpose": SLICE_PURPOSES[tag],
            "stats": stats(recs),
            "case_ids": ids,
        }
    out["selected_unique_total"] = len(selected_total)
    return out


SLICE_DEFINITIONS = {
    "S1_FIN_COMP": "category == 'Financials' AND type == 'Compositional'",
    "S2_FIN_NONQUANT_MULTI": "category == 'Financials' AND type in {None,'None'} AND n_refs >= 2",
    "S3_CO_COMP": "category == 'Company overview' AND type == 'Compositional'",
    "S4_CO_MULTI_NONQUANT": "category == 'Company overview' AND type in {None,'None'} AND n_refs >= 2",
    "S5_FN_MULTI": "category == 'Footnotes' AND n_refs >= 2",
    "S6_BASELINE_SINGLE": "category in target AND n_refs == 1 AND type != 'Compositional' (sampled 50/cat)",
}

SLICE_PURPOSES = {
    "S1_FIN_COMP": "Multi-year, multi-row arithmetic over financial statements — graph wins via FinancialMetric{Company,Year} nodes.",
    "S2_FIN_NONQUANT_MULTI": "Cross-statement (Income Stmt + Balance Sheet) qualitative synthesis — graph links same Company across statements.",
    "S3_CO_COMP": "Part-whole structural reasoning (Segment / Region / Role employee counts) — graph wins via HAS_SEGMENT relations.",
    "S4_CO_MULTI_NONQUANT": "Cross-segment narrative matrix (e.g., Product×EndUse). Vector likely misses chunks; graph traverses BELONGS_TO/USED_IN.",
    "S5_FN_MULTI": "10-K Item-spanning integration (table + accounting policy + reconciliation). Graph links Items via shared entities.",
    "S6_BASELINE_SINGLE": "Single-passage lookup with no integration / no compositional arithmetic — vector baseline territory; control slice.",
}


def write_summary_md(manifest: dict) -> None:
    md = OUT / "SLICES.md"
    lines: list[str] = []
    lines.append("# FinDER Selected Slices — Indexing Manifest")
    lines.append("")
    lines.append(f"- Source: `{manifest['source_parquet']}` ({manifest['source_total_rows']} rows)")
    lines.append(f"- Target categories: `{', '.join(manifest['target_categories'])}`")
    lines.append(f"- Baseline sample per category (S6): {manifest['baseline_sample_per_category']}")
    lines.append(f"- Selected unique total (after dedup): **{manifest['selected_unique_total']}**")
    lines.append("")
    lines.append("## Slice catalog")
    lines.append("")
    lines.append("| Slice | Definition | Count | n_refs (mean / max) | Purpose |")
    lines.append("|---|---|---:|---|---|")
    for tag, info in manifest["slices"].items():
        s = info["stats"]
        lines.append(
            f"| **{tag}** | `{info['definition']}` | {s['count']} | {s['n_refs']['mean']} / {s['n_refs']['max']} | {info['purpose']} |"
        )
    lines.append("")
    lines.append("## Per-slice detail")
    lines.append("")
    for tag, info in manifest["slices"].items():
        s = info["stats"]
        lines.append(f"### {tag}")
        lines.append(f"- **Definition**: `{info['definition']}`")
        lines.append(f"- **Purpose**: {info['purpose']}")
        lines.append(f"- **Count**: {s['count']}")
        lines.append(f"- **By category**: {s['by_category']}")
        lines.append(f"- **By type**: {s['by_type']}")
        lines.append(f"- **reasoning=True**: {s['reasoning_true_count']}")
        lines.append(f"- **n_refs**: min={s['n_refs']['min']}, mean={s['n_refs']['mean']}, median={s['n_refs']['median']}, max={s['n_refs']['max']}")
        lines.append(f"- **n_refs distribution**: {s['n_refs']['distribution']}")
        lines.append(f"- **Query length (words)**: min={s['query_words']['min']}, mean={s['query_words']['mean']}, max={s['query_words']['max']}")
        lines.append(f"- **Sample tickers in queries**: {', '.join(s['unique_companies_sampled'])}")
        lines.append("")
    md.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    # Determinism — slice membership depends on stratified sampling (S6)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from examples.finder.lib import bench_common as bc
    bc.set_global_determinism(SEED)

    df = pd.read_parquet(SRC)
    total = len(df)

    preds = slice_predicates(df)
    s1 = df[preds["S1_FIN_COMP"]]
    s2 = df[preds["S2_FIN_NONQUANT_MULTI"]]
    s3 = df[preds["S3_CO_COMP"]]
    s4 = df[preds["S4_CO_MULTI_NONQUANT"]]
    s5 = df[preds["S5_FN_MULTI"]]
    excl = set(s1["_id"]) | set(s2["_id"]) | set(s3["_id"]) | set(s4["_id"]) | set(s5["_id"])
    s6 = stratified_baseline(df, excl)

    slices = {
        "S1_FIN_COMP": s1,
        "S2_FIN_NONQUANT_MULTI": s2,
        "S3_CO_COMP": s3,
        "S4_CO_MULTI_NONQUANT": s4,
        "S5_FN_MULTI": s5,
        "S6_BASELINE_SINGLE": s6,
    }

    combined_frames = [_slice_records_to_df(recs, tag) for tag, recs in slices.items()]
    pd.concat(combined_frames, ignore_index=True).to_csv(
        OUT / "all_slices.csv", index=False, encoding="utf-8"
    )
    dump_previews(OUT / "previews.md", slices)

    manifest = build_manifest(slices, total)
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_summary_md(manifest)

    print(json.dumps(
        {
            "source_total": total,
            "selected_unique": manifest["selected_unique_total"],
            "slices": {k: v["stats"]["count"] for k, v in manifest["slices"].items()},
            "output_dir": str(OUT.relative_to(ROOT)),
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
