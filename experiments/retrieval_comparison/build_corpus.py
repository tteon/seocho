"""Build a 20-doc combined corpus + an auto-generated query set.

Combines the two existing labelled datasets:
  - examples/finder/datasets/finder_tutorial_subset.json (10 docs)
  - examples/datasets/tutorial_filings_sample.json     (10 docs)

Writes:
  - experiments/retrieval_comparison/corpus.json   — combined documents
  - experiments/retrieval_comparison/queries.auto.jsonl
        — 1 query per doc + manually authored cross-document probes.
          Each row has gold_chunks for hit@k metrics.

Run once before seeding::

    python3 -m experiments.retrieval_comparison.build_corpus
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

EXP_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXP_DIR.parent.parent

SRC_FINDER = REPO_ROOT / "examples" / "finder" / "datasets" / "finder_tutorial_subset.json"
SRC_FILINGS = REPO_ROOT / "examples" / "datasets" / "tutorial_filings_sample.json"

OUT_CORPUS = EXP_DIR / "corpus.json"
OUT_QUERIES = EXP_DIR / "queries.auto.jsonl"


# Hand-curated cross-document queries — each gold_chunks list points to all
# docs the answer should pull from. The expected_favored_mode is what we
# think *should* win this query a priori; the report cross-checks vs actual.
CROSS_DOC_QUERIES: List[Dict[str, Any]] = [
    {
        "id": "x01",
        "query": "Which companies are headquartered in California?",
        "category": "property_filter",
        "expected_favored_mode": "graph",
        "gold_chunks": ["finder_tut_001", "finder_tut_009"],
        "notes": "Apple (Cupertino), Amazon HQ mentioned. Cross-doc filter.",
    },
    {
        "id": "x02",
        "query": "Who are the CEOs across the corpus?",
        "category": "property_enumerate",
        "expected_favored_mode": "graph",
        "gold_chunks": ["case_003", "finder_tut_009"],
        "notes": "Alphabet (Pichai), Amazon (Jassy) — enumerate CEO role across companies.",
    },
    {
        "id": "x03",
        "query": "Show all filings that mention fiscal year 2023 revenue figures",
        "category": "topic_filter",
        "expected_favored_mode": "hybrid",
        "gold_chunks": ["case_001", "case_004", "case_006", "case_007", "case_009", "finder_tut_002", "finder_tut_003", "finder_tut_006", "finder_tut_007", "finder_tut_010"],
        "notes": "Many docs mention 2023 revenue; vector retrieves topic, graph confirms FinancialMetric nodes.",
    },
    {
        "id": "x04",
        "query": "Tell me about Apple",
        "category": "entity_aggregation",
        "expected_favored_mode": "hybrid",
        "gold_chunks": ["case_007", "finder_tut_001", "finder_tut_010"],
        "notes": "Apple appears in 3 docs (shareholder return, HQ, Berkshire investment).",
    },
    {
        "id": "x05",
        "query": "Which companies are mentioned with regulatory or antitrust concerns?",
        "category": "semantic_filter",
        "expected_favored_mode": "vector",
        "gold_chunks": ["case_005", "case_008", "finder_tut_005"],
        "notes": "MSFT antitrust, Amazon regulatory, Meta Cambridge Analytica.",
    },
    {
        "id": "x06",
        "query": "What stake does Berkshire Hathaway hold in Apple?",
        "category": "relationship_lookup",
        "expected_favored_mode": "hybrid",
        "gold_chunks": ["finder_tut_010"],
        "notes": "Single-doc relationship lookup with named entities both sides.",
    },
    {
        "id": "x07",
        "query": "Find all companies that disclose climate-related risks",
        "category": "unanswerable",
        "expected_favored_mode": "none",
        "gold_chunks": [],
        "notes": "No doc mentions climate risk; should return empty/low confidence.",
    },
    {
        "id": "x08",
        "query": "Which executives appear in more than one company's filings?",
        "category": "multi_hop",
        "expected_favored_mode": "graph",
        "gold_chunks": [],
        "notes": "Across 20 docs, expected mostly empty (each filing has distinct execs).",
    },
]


def main() -> int:
    finder = json.loads(SRC_FINDER.read_text())
    filings = json.loads(SRC_FILINGS.read_text())

    # Normalize to a common shape. Both already share {id, text, question,
    # expected_answer, category}. Just guarantee distinct ids.
    finder_ids = {item["id"] for item in finder}
    filings_ids = {item["id"] for item in filings}
    overlap = finder_ids & filings_ids
    assert not overlap, f"Unexpected id collision between datasets: {overlap}"

    combined: List[Dict[str, Any]] = []
    for item in [*finder, *filings]:
        combined.append({
            "id": item["id"],
            "text": item["text"],
            "category": item.get("category", ""),
            "question": item.get("question", ""),
            "expected_answer": item.get("expected_answer", ""),
            "reasoning_type": item.get("reasoning_type", ""),
        })

    OUT_CORPUS.write_text(json.dumps(combined, indent=2, ensure_ascii=False))
    print(f"[corpus] wrote {OUT_CORPUS} — {len(combined)} docs")

    # Build per-doc queries (single gold_chunk = that doc) + cross-doc.
    queries: List[Dict[str, Any]] = []
    for item in combined:
        if not item.get("question"):
            continue
        queries.append({
            "id": f"d_{item['id']}",
            "query": item["question"],
            "category": f"single_doc_{(item.get('category') or 'general').lower().replace(' ', '_')}",
            "expected_favored_mode": "vector",  # single-doc lookups generally favor vector
            "gold_chunks": [item["id"]],
            "notes": f"Auto-generated from {item['id']} question.",
        })

    queries.extend(CROSS_DOC_QUERIES)

    with OUT_QUERIES.open("w") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"[corpus] wrote {OUT_QUERIES} — {len(queries)} queries "
          f"({len(combined)} single-doc + {len(CROSS_DOC_QUERIES)} cross-doc)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
