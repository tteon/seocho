#!/usr/bin/env python3
"""Would mixing the categories into one graph merge things that are not the same?

The corpus is split into eight categories and each was extracted into its own
database. That is a cost — twelve databases for one experiment, and an instance
that hit its cap — so it needs a justification stronger than caution.

The justification would be this: the graph merges on name, so two nodes called
"interest" written by two different categories become one node. If the two
categories mean the same thing by "interest", merging is correct and the
separation was wasted. If they mean different things, merging silently fuses two
facts and every count downstream is wrong.

So the question is not whether names collide. It is whether colliding names mean
the same thing. Two measurements, and the second is the one that matters:

  overlap     how many entity names, and relationship types, appear in more
              than one category at all
  divergence  for those names, how similar the surrounding graph context is
              between categories, using local embeddings

A name that appears everywhere with the same context is safe to merge. A name
that appears everywhere with different context is exactly the contamination the
separation prevents, and the gap between those two populations is the evidence.

Reads the per-category databases built earlier (mdmcat*). Local BGE embeddings,
no API call.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from dotenv import dotenv_values  # noqa: E402

for _key, _value in dotenv_values(ROOT / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)

URI = "bolt://localhost:7687"
OUT_ROOT = ROOT / "outputs/minimal"
MODEL = "BAAI/bge-small-en-v1.5"

CATEGORY_DB = {
    "Accounting": "mdmcataccounting",
    "Company overview": "mdmcatcompany",
    "Financials": "mdmcatfinancials",
    "Footnotes": "mdmcatfootnotes",
    "Governance": "mdmcatgovernance",
    "Legal": "mdmcatlegal",
    "Risk": "mdmcatrisk",
    "Shareholder return": "mdmcatshareholder",
}
INFRA = {"Document", "Chunk", "Version", "DocumentVersion", "Section",
         "__Memory__", "Memory"}

# Names the extractor produced that are not entities: articles, pronouns, bare
# months. They collide across every category for a reason that has nothing to do
# with meaning, so they are counted, reported, and then excluded. That they
# exist at all is a finding about extraction quality, not about contamination.
DEGENERATE = {
    "the", "our", "we", "us", "it", "its", "their", "this", "that", "these",
    "those", "for", "and", "or", "of", "in", "on", "at", "to", "a", "an",
    "company", "the company", "companies", "corporation", "inc", "llc",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "total", "other", "n a",
}


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def context_of(row: dict[str, Any]) -> str:
    """What the graph says around a node, as the text an embedding can read.

    Label, value and neighbour names. This is the 'meaning in context' the
    merge would destroy, so it is what has to be compared.
    """
    parts = [str(row.get("name", ""))]
    labels = [l for l in (row.get("labels") or []) if l not in INFRA]
    if labels:
        parts.append("a " + ", ".join(sorted(labels)))
    if str(row.get("value") or "").strip():
        parts.append("value " + str(row["value"]))
    neighbours = [n for n in (row.get("neighbours") or []) if n][:8]
    if neighbours:
        parts.append("related to " + ", ".join(sorted(set(neighbours))))
    return "; ".join(p for p in parts if p)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-category-limit", type=int, default=4000)
    ap.add_argument("--sample-shared", type=int, default=400,
                    help="how many shared names to embed; embedding is the "
                         "slow step and the estimate is stable well below the "
                         "full set")
    args = ap.parse_args()

    import observe
    from neo4j import GraphDatabase

    run = observe.Run(OUT_ROOT, "category-contamination", {"decisive": {
        "categories": sorted(CATEGORY_DB),
        "embedding_model": MODEL,
        "per_category_limit": args.per_category_limit,
        "sample_shared": args.sample_shared, "seed": 42}})

    import parallel

    driver = GraphDatabase.driver(URI, auth=auth())
    per_category: dict[str, dict[str, dict[str, Any]]] = {}
    rel_types: dict[str, Counter] = {}

    def read_category(item: tuple[str, str]) -> dict[str, Any]:
        """Eight databases, eight threads. Bolt-bound, so threads are right."""
        category, database = item
        with driver.session(database=database) as session:
            rows = session.run(
                "MATCH (n) WHERE n.name IS NOT NULL "
                "AND NOT any(l IN labels(n) WHERE l IN $infra) "
                "OPTIONAL MATCH (n)--(m) WHERE m.name IS NOT NULL "
                "WITH n, collect(DISTINCT m.name)[0..8] AS neighbours "
                "RETURN labels(n) AS labels, n.name AS name, "
                "       coalesce(n.value, n.amount, '') AS value, "
                "       neighbours LIMIT $limit",
                infra=sorted(INFRA), limit=args.per_category_limit).data()
            types = session.run(
                "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c").data()
        by_name: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = normalize(row["name"])
            if key and key not in by_name:
                by_name[key] = row
        return {"category": category, "by_name": by_name, "nodes": len(rows),
                "types": Counter({r["t"]: r["c"] for r in types})}

    try:
        with run.stage("read", categories=len(CATEGORY_DB)) as out:
            for result in parallel.io_map(read_category,
                                          sorted(CATEGORY_DB.items())):
                if result is None:
                    continue
                per_category[result["category"]] = result["by_name"]
                rel_types[result["category"]] = result["types"]
            out["categories_read"] = len(per_category)
            out["nodes_total"] = sum(len(v) for v in per_category.values())
    finally:
        driver.close()

    with run.stage("overlap") as out:
        appears_in: dict[str, list[str]] = defaultdict(list)
        for category, by_name in per_category.items():
            for name in by_name:
                appears_in[name].append(category)
        shared_all = {n: c for n, c in appears_in.items() if len(c) > 1}
        shared = {n: c for n, c in shared_all.items() if n not in DEGENERATE}
        degenerate_shared = len(shared_all) - len(shared)
        rel_appears: dict[str, list[str]] = defaultdict(list)
        for category, types in rel_types.items():
            for rtype in types:
                rel_appears[rtype].append(category)
        shared_rels = {t: c for t, c in rel_appears.items() if len(c) > 1}
        out["distinct_names_total"] = len(appears_in)
        out["names_in_more_than_one_category_raw"] = len(shared_all)
        out["excluded_as_non_entities"] = degenerate_shared
        out["names_in_more_than_one_category"] = len(shared)
        out["name_overlap_rate"] = round(len(shared) / len(appears_in), 4)
        out["relationship_types_total"] = len(rel_appears)
        out["relationship_types_shared"] = len(shared_rels)
        out["most_shared"] = [f"{n} ({len(c)})" for n, c in
                              sorted(shared.items(), key=lambda kv: -len(kv[1]))[:10]]

    with run.stage("embed", model=MODEL) as out:
        from sentence_transformers import SentenceTransformer
        import numpy as np

        encoder = SentenceTransformer(MODEL)
        ordered = sorted(shared.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        chosen = ordered[:args.sample_shared]
        texts, owners = [], []
        for name, categories in chosen:
            for category in categories:
                texts.append(context_of(per_category[category][name]))
                owners.append((name, category))
        vectors = encoder.encode(texts, normalize_embeddings=True,
                                 batch_size=128, show_progress_bar=False)
        out["names_embedded"] = len(chosen)
        out["contexts_embedded"] = len(texts)

    with run.stage("divergence") as out:
        import numpy as np

        index = {pair: i for i, pair in enumerate(owners)}
        per_name = []
        for name, categories in chosen:
            sims = []
            for left, right in combinations(sorted(categories), 2):
                a = vectors[index[(name, left)]]
                b = vectors[index[(name, right)]]
                sims.append(float(a @ b))
            if sims:
                per_name.append({"name": name, "categories": len(categories),
                                 "mean_similarity": round(sum(sims) / len(sims), 4),
                                 "min_similarity": round(min(sims), 4)})
        values = [p["mean_similarity"] for p in per_name]

        # The control: unrelated names inside one category. If shared names are
        # no more similar than two arbitrary nodes, the collision carries no
        # meaning at all and merging is pure noise.
        control = []
        for category, by_name in per_category.items():
            names = sorted(by_name)[:40]
            if len(names) < 2:
                continue
            texts = [context_of(by_name[n]) for n in names]
            vecs = encoder.encode(texts, normalize_embeddings=True,
                                  batch_size=128, show_progress_bar=False)
            sim = vecs @ vecs.T
            np.fill_diagonal(sim, np.nan)
            control.append(float(np.nanmean(sim)))

        low = [p for p in per_name if p["mean_similarity"] < 0.6]
        out["shared_names_scored"] = len(per_name)
        out["mean_similarity"] = round(float(np.mean(values)), 4) if values else 0.0
        out["median_similarity"] = round(float(np.median(values)), 4) if values else 0.0
        out["share_below_0_6"] = round(len(low) / len(per_name), 4) if per_name else 0.0
        out["control_unrelated_within_category"] = (round(float(np.mean(control)), 4)
                                                    if control else 0.0)

    worst = sorted(per_name, key=lambda p: p["mean_similarity"])[:15]
    payload = {
        "contract": "log2026.category_contamination.v1",
        "question": ("If the categories shared one graph, would name collisions "
                     "merge things that mean different things?"),
        "method": ("distinct entity names and relationship types per category "
                   f"database; for names present in more than one category, "
                   f"cosine similarity between their graph contexts using local "
                   f"{MODEL}; control is the similarity of unrelated nodes "
                   f"inside a single category"),
        "claim_boundary": ("Context similarity is a proxy for meaning, computed "
                           "from the extracted graph rather than the source "
                           "text. It shows that colliding names sit in different "
                           "surroundings; it does not prove a specific merge "
                           "would produce a specific wrong answer."),
        "categories": len(per_category),
        "distinct_names_total": len(appears_in),
        "names_in_more_than_one_category_raw": len(shared_all),
        "excluded_as_non_entities": degenerate_shared,
        "names_in_more_than_one_category": len(shared),
        "name_overlap_rate": round(len(shared) / len(appears_in), 4),
        "relationship_types_total": len(rel_appears),
        "relationship_types_shared": len(shared_rels),
        "shared_names_scored": len(per_name),
        "mean_context_similarity": (round(float(sum(values) / len(values)), 4)
                                    if values else 0.0),
        "share_below_0_6": (round(len(low) / len(per_name), 4) if per_name else 0.0),
        "control_unrelated_within_category": out["control_unrelated_within_category"],
        "most_divergent": worst,
        "shared_relationship_types": sorted(shared_rels)[:40],
    }
    (run.dir / "category_contamination.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"categories                                {len(per_category)}")
    print(f"distinct entity names                     {len(appears_in):,}")
    print(f"  in more than one category               {len(shared_all):,} raw, "
          f"{len(shared):,} after removing {degenerate_shared} non-entities "
          f"({len(shared) / len(appears_in):.1%})")
    print(f"relationship types                        {len(rel_appears)}")
    print(f"  in more than one category               {len(shared_rels)}")
    print(f"shared names scored for context           {len(per_name)}")
    print(f"  mean context similarity                 {payload['mean_context_similarity']:.3f}")
    print(f"  share below 0.6                         {payload['share_below_0_6']:.1%}")
    print(f"  control, unrelated nodes same category  "
          f"{payload['control_unrelated_within_category']:.3f}")
    print("\nsame name, most different surroundings:")
    for entry in worst[:8]:
        print(f"  {entry['mean_similarity']:.3f}  {entry['name'][:56]} "
              f"({entry['categories']} categories)")

    run.finish({"name_overlap_rate": payload["name_overlap_rate"],
                "mean_context_similarity": payload["mean_context_similarity"],
                "share_below_0_6": payload["share_below_0_6"],
                "artifact": str((run.dir / "category_contamination.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
