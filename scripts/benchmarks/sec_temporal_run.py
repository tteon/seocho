#!/usr/bin/env python3
"""SEC temporal benchmark runner — the three prior-resistance A/Bs.

Consumes the JSONL from ``sec_temporal_bench.py`` and measures, per question:

1. **closed-book** — the LLM answers from priors alone (no graph).
2. **grounded**   — the corpus (FY-labelled fact sentences) is indexed into the
   graph and ``ask()`` answers from retrieval (chunk fallback ON, since these
   short fact sentences rarely produce a structured Cypher hit — this is the
   path that lets the graph's chunk store actually contribute).

From those two it derives the three A/Bs the user asked for:

- **closed-book vs grounded**: value-match accuracy of each arm.
- **prior-staleness correction**: the same two accuracies restricted to
  ``prior_stale`` (post-cutoff FY2025) rows — where the prior provably cannot
  know the figure, so any grounded lift is unambiguous graph contribution.
- **temporal resolution**: for grounded answers, did it return the *asked*
  year's value and NOT a different year's value present in the same corpus?

Value matching is numeric and scale-aware ($416,161 million ≡ $416.2 billion ≡
416161000000) so the verbose grounded answer and a rounded closed-book answer
are scored on equal footing. The scoring logic is pure and unit-tested; only
``run()`` touches the network / DozerDB.

Usage::

    SEOCHO_CHUNK_FALLBACK=1 python scripts/benchmarks/sec_temporal_run.py \\
        --dataset outputs/evaluation/sec_temporal/dataset.jsonl \\
        --limit-tickers 3 \\
        --out outputs/evaluation/sec_temporal/run.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Pure scoring logic (no network, no LLM) — unit-tested
# ---------------------------------------------------------------------------

_SCALE = {
    "thousand": 1e3,
    "k": 1e3,
    "million": 1e6,
    "mm": 1e6,
    "m": 1e6,
    "billion": 1e9,
    "bn": 1e9,
    "b": 1e9,
    "trillion": 1e12,
    "t": 1e12,
}

# A number (commas / decimal) optionally followed by a scale word/suffix.
_NUM_RE = re.compile(
    r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*"
    r"(thousand|million|billion|trillion|mm|bn|[kmbt])?\b",
    re.IGNORECASE,
)


def extract_usd_values(text: str) -> List[float]:
    """Pull every dollar-ish quantity from text as an absolute USD float.

    Handles ``$416,161 million``, ``416.2 billion``, ``$391B``, and bare large
    integers like ``416161000000``. Scale words/suffixes apply their multiplier;
    a bare number is taken at face value.
    """
    out: List[float] = []
    for m in _NUM_RE.finditer(text or ""):
        raw, scale = m.group(1), (m.group(2) or "").lower()
        try:
            base = float(raw.replace(",", ""))
        except ValueError:
            continue
        out.append(base * _SCALE.get(scale, 1.0))
    return out


def value_matches(text: str, raw_value: Any, *, rel_tol: float = 0.02) -> bool:
    """True if any quantity in ``text`` matches ``raw_value`` within ``rel_tol``.

    Scale-aware and rounding-tolerant so ``$416,161 million`` (grounded, verbose)
    and ``$416.2 billion`` (closed-book, rounded) both match the gold raw value.
    """
    if not isinstance(raw_value, (int, float)) or raw_value == 0:
        return False
    target = abs(float(raw_value))
    for v in extract_usd_values(text):
        if abs(v - target) <= rel_tol * target:
            return True
    return False


def temporal_verdict(
    text: str,
    asked_value: Any,
    other_year_values: List[Any],
    *,
    rel_tol: float = 0.02,
) -> str:
    """Classify a grounded answer's temporal correctness.

    - ``correct``       : matches the asked year's value.
    - ``wrong_year``    : matches some *other* year present in the corpus
                          (the lane retrieved a fact but the wrong period).
    - ``no_match``      : matches neither (empty/unsupported answer).

    ``correct`` wins ties (an answer that states multiple years still counts
    as resolving the asked one).
    """
    if value_matches(text, asked_value, rel_tol=rel_tol):
        return "correct"
    for ov in other_year_values:
        if value_matches(text, ov, rel_tol=rel_tol):
            return "wrong_year"
    return "no_match"


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll per-question records up into the three A/Bs."""

    def acc(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
        return round(sum(1 for r in rows if r[key]) / len(rows), 3) if rows else None

    stale = [r for r in records if r["prior_stale"]]
    fresh = [r for r in records if not r["prior_stale"]]
    grounded = [r for r in records if r.get("temporal") is not None]

    temporal_counts: Dict[str, int] = defaultdict(int)
    for r in grounded:
        temporal_counts[r["temporal"]] += 1

    return {
        "n": len(records),
        "closed_book_vs_grounded": {
            "closed_book_acc": acc(records, "closed_book_match"),
            "grounded_acc": acc(records, "grounded_match"),
        },
        "prior_staleness": {
            "stale_n": len(stale),
            "stale_closed_book_acc": acc(stale, "closed_book_match"),
            "stale_grounded_acc": acc(stale, "grounded_match"),
            "fresh_closed_book_acc": acc(fresh, "closed_book_match"),
            "fresh_grounded_acc": acc(fresh, "grounded_match"),
        },
        "temporal_resolution": {
            "grounded_n": len(grounded),
            "correct": temporal_counts.get("correct", 0),
            "wrong_year": temporal_counts.get("wrong_year", 0),
            "no_match": temporal_counts.get("no_match", 0),
            "resolution_rate": (
                round(temporal_counts.get("correct", 0) / len(grounded), 3)
                if grounded
                else None
            ),
        },
    }


# ---------------------------------------------------------------------------
# Impure runner (network + DozerDB + LLM)
# ---------------------------------------------------------------------------

CLOSED_BOOK_SYSTEM = (
    "You are a financial analyst. Answer with the specific reported dollar "
    "figure for the exact fiscal year asked. Be concise; state the number."
)


def _finance_ontology() -> Any:
    from seocho import Ontology, NodeDef, RelDef, P

    return Ontology(
        name="sec_temporal",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "Metric": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={"REPORTED": RelDef(source="Company", target="Metric")},
    )


def run(
    dataset_path: str,
    *,
    limit_tickers: Optional[int],
    database: str,
    uri: str,
    user: str,
    password: str,
    provider: str,
    model: str,
) -> Dict[str, Any]:
    from seocho import Seocho
    from seocho.store.graph import Neo4jGraphStore
    from seocho.store.llm import create_llm_backend

    rows = []
    with Path(dataset_path).open("r", encoding="utf-8") as f:
        for l in f:
            if l.strip():
                rows.append(json.loads(l))

    # group by (ticker, metric): index the shared multi-year corpus once,
    # then ask each year's question against it (temporal-resolution setup).
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(r["ticker"], r["metric"])].append(r)
    if limit_tickers:
        keep = sorted({k[0] for k in groups})[:limit_tickers]
        groups = {k: v for k, v in groups.items() if k[0] in keep}

    llm = create_llm_backend(provider=provider, model=model)
    graph_store = Neo4jGraphStore(uri=uri, user=user, password=password)

    # fresh bench DB
    with graph_store._driver.session(database="system") as s:
        s.run(f"CREATE DATABASE {database} IF NOT EXISTS")
    time.sleep(1.0)
    with graph_store._driver.session(database=database) as s:
        s.run("MATCH (n) DETACH DELETE n")

    ontology = _finance_ontology()
    records: List[Dict[str, Any]] = []

    for (ticker, metric), grp in sorted(groups.items()):
        ws = f"{ticker}_{metric}".lower().replace("-", "_")
        client = Seocho(
            ontology=ontology,
            graph_store=graph_store,
            llm=llm,
            workspace_id=ws,
        )
        corpus = grp[0]["corpus"]  # identical across the group's rows
        for doc in corpus:
            try:
                client.add(content=doc, database=database, category="sec_fact")
            except Exception as exc:  # noqa: BLE001
                print(f"  [ingest-fail] {ws}: {exc}", file=sys.stderr)

        years_in_corpus = {r["fiscal_year"]: r["raw_value"] for r in grp}
        for r in grp:
            # closed-book (prior only)
            try:
                cb = llm.complete(system=CLOSED_BOOK_SYSTEM, user=r["question"]).text
            except Exception as exc:  # noqa: BLE001
                cb = f"<error: {exc}>"
            # grounded (graph)
            try:
                gr = client.ask(r["question"], database=database)
            except Exception as exc:  # noqa: BLE001
                gr = f"<error: {exc}>"

            others = [v for y, v in years_in_corpus.items() if y != r["fiscal_year"]]
            rec = {
                "ticker": ticker,
                "metric": metric,
                "fiscal_year": r["fiscal_year"],
                "prior_stale": r["prior_stale"],
                "gold": r["answer"],
                "raw_value": r["raw_value"],
                "closed_book": cb.strip()[:300],
                "grounded": gr.strip()[:300],
                "closed_book_match": value_matches(cb, r["raw_value"]),
                "grounded_match": value_matches(gr, r["raw_value"]),
                "temporal": temporal_verdict(gr, r["raw_value"], others),
            }
            records.append(rec)
            print(
                f"  [{ticker} {metric} FY{r['fiscal_year']}"
                f"{' STALE' if r['prior_stale'] else ''}] "
                f"cb={'Y' if rec['closed_book_match'] else 'n'} "
                f"gr={'Y' if rec['grounded_match'] else 'n'} "
                f"temporal={rec['temporal']}",
                file=sys.stderr,
            )

    try:
        graph_store.close()
    except Exception:
        pass

    return {
        "config": {
            "dataset": dataset_path,
            "database": database,
            "provider": provider,
            "model": model,
            "chunk_fallback": os.environ.get("SEOCHO_CHUNK_FALLBACK", ""),
        },
        "summary": aggregate(records),
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SEC temporal benchmark runner")
    parser.add_argument(
        "--dataset", default="outputs/evaluation/sec_temporal/dataset.jsonl"
    )
    parser.add_argument(
        "--limit-tickers",
        type=int,
        default=None,
        help="Run only the first N tickers (smoke)",
    )
    parser.add_argument("--database", default="sectemporal")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="neo4jpassword")
    parser.add_argument("--provider", default="mara")
    parser.add_argument("--model", default="MiniMax-M2.5")
    parser.add_argument("--out", default="-")
    args = parser.parse_args()

    result = run(
        args.dataset,
        limit_tickers=args.limit_tickers,
        database=args.database,
        uri=args.uri,
        user=args.user,
        password=args.password,
        provider=args.provider,
        model=args.model,
    )

    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(out)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote results to {args.out}", file=sys.stderr)

    s = result["summary"]
    print("\n=== SEC temporal A/B ===", file=sys.stderr)
    print(f"closed-book vs grounded: {s['closed_book_vs_grounded']}", file=sys.stderr)
    print(f"prior-staleness:         {s['prior_staleness']}", file=sys.stderr)
    print(f"temporal-resolution:     {s['temporal_resolution']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
