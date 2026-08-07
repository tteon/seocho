#!/usr/bin/env python3
"""How much do category views share entities, and does the ontology govern it?

The fact-level census found that only 8.0% of extracted facts are named the same
way by two providers, which caps what any cross-view verifier can see. This asks
the same question one level up, across the eight organizational views rather than
across providers:

  1. entity overlap        how many entities appear in two or more category views
  2. ontology dependence   whether ontology-declared types overlap more than the
                           generic fallback type
  3. pair structure        which views actually share entities with which

Read together with the already-measured context divergence for shared entities
(mean PPR@20 divergence .986, median 1.000), this separates two different
ceilings: a view pair can fail to share an entity at all, or share it and still
retrieve disjoint neighborhoods. The first bounds federation structurally; the
second is what makes federation worth doing when the first is satisfied.

Reads the frozen collapsed category graph. No database, model, or embedding
calls. Outputs outputs/evaluation/mdm_fedcat/log2026-entity-overlap-v1/.
"""
from __future__ import annotations

import collections
import gzip
import json
import re
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-multiagent-network-v1/collapsed_graph.json.gz"
DIVERGENCE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-entity-cleaning-ablation-v1/analysis.json"
OUT = ROOT / "outputs/evaluation/mdm_fedcat/log2026-entity-overlap-v1"

# Structural labels are containers, not entities; counting them as shared would
# inflate overlap with document plumbing.
STRUCTURAL = {"Chunk", "Document", "Section"}
# The generic fallback the no-ontology arm produces. Comparing it against
# declared types is the ontology-dependence test.
GENERIC = "Entity"
PLACEHOLDER = {"issuer", "our", "the company", "company", "registrant", "we",
               "us", "it", "they", "n/a", "none", "unknown", ""}


def norm_name(name: str) -> str:
    text = re.sub(r"\s+", " ", str(name).strip().lower())
    text = re.sub(r"[^a-z0-9 &.\-]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    if not SRC.is_file():
        raise SystemExit(f"missing frozen graph {SRC}")
    graph = json.loads(gzip.open(SRC).read())
    nodes = graph["nodes"]

    # entity -> categories, and entity -> ontology labels
    cats: dict[str, set[str]] = collections.defaultdict(set)
    labels: dict[str, set[str]] = collections.defaultdict(set)
    per_category: dict[str, set[str]] = collections.defaultdict(set)
    skipped_structural = skipped_placeholder = 0

    for node in nodes:
        node_labels = {l for l in node.get("labels", []) if l}
        if node_labels & STRUCTURAL:
            skipped_structural += 1
            continue
        name = norm_name(node.get("name", ""))
        if not name or name in PLACEHOLDER:
            skipped_placeholder += 1
            continue
        category = str(node.get("category", "")).strip()
        if not category:
            continue
        cats[name].add(category)
        labels[name] |= node_labels
        per_category[category].add(name)

    total = len(cats)
    shared = {n: c for n, c in cats.items() if len(c) >= 2}
    spread = collections.Counter(len(c) for c in cats.values())

    # 2. ontology dependence: declared type vs generic fallback
    def rate(predicate) -> dict[str, float | int]:
        pool = [n for n in cats if predicate(labels[n])]
        multi = [n for n in pool if len(cats[n]) >= 2]
        return {"entities": len(pool), "in_two_or_more_views": len(multi),
                "overlap_rate": round(len(multi) / len(pool), 6) if pool else 0.0}

    by_type = {
        "declared_type_only": rate(lambda ls: bool(ls - {GENERIC}) and GENERIC not in ls),
        "generic_fallback_only": rate(lambda ls: ls == {GENERIC}),
        "any_declared_type": rate(lambda ls: bool(ls - {GENERIC})),
    }
    per_label = {}
    label_counts = collections.Counter()
    for name, ls in labels.items():
        for l in ls:
            label_counts[l] += 1
    for label, count in label_counts.most_common(12):
        pool = [n for n in cats if label in labels[n]]
        multi = [n for n in pool if len(cats[n]) >= 2]
        per_label[label] = {"entities": len(pool),
                            "in_two_or_more_views": len(multi),
                            "overlap_rate": round(len(multi) / len(pool), 6) if pool else 0.0}

    # 3. which views share with which
    matrix = {}
    for a, b in combinations(sorted(per_category), 2):
        sa, sb = per_category[a], per_category[b]
        inter = len(sa & sb)
        union = len(sa | sb)
        matrix[f"{a}|{b}"] = {"shared": inter, "union": union,
                              "jaccard": round(inter / union, 6) if union else 0.0}

    context = json.loads(DIVERGENCE.read_text())["after"]
    payload = {
        "contract": "log2026.entity_overlap_census.v1",
        "source": "frozen collapsed category graph; no database or model calls",
        "normalization": ("case-folded, punctuation stripped; structural labels "
                          f"{sorted(STRUCTURAL)} and placeholder names excluded"),
        "claim_boundary": ("Overlap is name-identity within the collapsed graph, a "
                           "lower bound: an alias the identifier policy would merge "
                           "counts as two entities here. It measures whether views "
                           "can be joined at all, not whether the join is correct."),
        "summary": {
            "nodes_read": len(nodes),
            "skipped_structural": skipped_structural,
            "skipped_placeholder_or_unnamed": skipped_placeholder,
            "distinct_entities": total,
            "entities_in_two_or_more_views": len(shared),
            "entity_overlap_rate": round(len(shared) / total, 6) if total else 0.0,
            "view_spread_histogram": {f"{k}_views": v for k, v in sorted(spread.items())},
            "by_ontology_status": by_type,
            "by_label": per_label,
            "view_pair_jaccard": matrix,
            "context_divergence_for_shared_entities": {
                "rank_weighted_cross_view_mean": context["cross_mean"],
                "matched_null_mean": context["null_mean"],
                "auroc": context["auroc"],
                "note": "from the identifier-resolved arm; shared entities retrieve "
                        "near-disjoint neighborhoods across views",
            },
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "entity_overlap_census.json").write_text(json.dumps(payload, indent=2) + "\n")

    s = payload["summary"]
    lines = [
        "# Entity Overlap Across Category Views (zero cost)", "",
        f"- Nodes read: {s['nodes_read']:,} "
        f"(structural skipped {s['skipped_structural']:,}, "
        f"unnamed or placeholder {s['skipped_placeholder_or_unnamed']:,})",
        f"- Distinct entities: {s['distinct_entities']:,}",
        f"- In two or more views: {s['entities_in_two_or_more_views']:,}",
        f"- **Entity overlap rate: {s['entity_overlap_rate']:.3f}**", "",
        "| Views containing the entity | Entities |", "|---|---:|",
    ]
    for k, v in sorted(spread.items()):
        lines.append(f"| {k} | {v:,} |")
    lines += ["", "## Does the ontology govern overlap?", "",
              "| Entity typing | Entities | In 2+ views | Overlap rate |", "|---|---:|---:|---:|"]
    for k, v in by_type.items():
        lines.append(f"| {k} | {v['entities']:,} | {v['in_two_or_more_views']:,} | "
                     f"{v['overlap_rate']:.3f} |")
    lines += ["", "| Ontology label | Entities | In 2+ views | Overlap rate |", "|---|---:|---:|---:|"]
    for k, v in per_label.items():
        lines.append(f"| {k} | {v['entities']:,} | {v['in_two_or_more_views']:,} | "
                     f"{v['overlap_rate']:.3f} |")
    top = sorted(matrix.items(), key=lambda kv: -kv[1]["jaccard"])
    lines += ["", "## Which views share entities?", "",
              "| View pair | Shared | Jaccard |", "|---|---:|---:|"]
    for k, v in top[:8]:
        lines.append(f"| {k} | {v['shared']:,} | {v['jaccard']:.4f} |")
    lines += ["", "## For entities that are shared, does context differ?", "",
              f"- Cross-view rank-weighted divergence {context['cross_mean']:.3f} "
              f"vs matched null {context['null_mean']:.3f} (AUROC {context['auroc']:.3f})",
              "- Shared entities retrieve near-disjoint neighborhoods, so overlap of "
              "identity does not imply overlap of context.", "",
              payload["claim_boundary"], ""]
    (OUT / "entity_overlap_census.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
