"""FinDER benchmark loader (HuggingFace ``Linq-AI-Research/FinDER``).

FinDER (ICLR'25) — 5,703 expert-annotated QA records grounded in 10-K SEC
filings (S&P 500, 2024). 8 categories.

Schema (HuggingFace raw)
------------------------
::

    _id          str   — record id
    text         str   — the *question* (short)
    references   list[str] — 10-K excerpts (the retrieval context)
    answer       str   — ground truth answer
    category     str   — one of 8 categories (may include spaces)
    reasoning    str   — "True" / "False" — whether reasoning is required
    type         str   — operation type ("Subtract", "True" qualitative, ...)

This loader **normalizes** each row to the more notebook-friendly shape:

::

    id            str   — copy of _id
    question      str   — copy of text
    document_text str   — references joined with "\\n\\n"
    text          str   — alias of document_text (so notebook code that
                          indexes ``doc['text']`` sees the *document body*,
                          not the question)
    answer        str   — passthrough
    category      str   — normalized to no-space form (e.g. CompanyOverview)
    reasoning_required bool — derived from reasoning == "True"
    type          str   — passthrough (reasoning subtype)
    references    list[str] — passthrough
    _id, _raw_text         — original fields preserved for debuggers
"""

from __future__ import annotations

import os
import random
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional


CATEGORIES: tuple[str, ...] = (
    "Accounting",
    "CompanyOverview",
    "Financials",
    "Footnotes",
    "Governance",
    "Legal",
    "Risk",
    "ShareholderReturn",
)

DEFAULT_HF_REPO = os.getenv("FINDER_HF_REPO", "Linq-AI-Research/FinDER")
DEFAULT_HF_SUBSET = os.getenv("FINDER_HF_SUBSET", "")  # no subset
DEFAULT_HF_SPLIT = os.getenv("FINDER_HF_SPLIT", "train")


def _cache_dir() -> Path:
    raw = os.getenv("SEOCHO_DATASET_CACHE_DIR") or os.getenv("FINDER_CACHE_DIR") or "./data"
    return Path(raw)


def _normalize_category(raw: Optional[str]) -> str:
    if raw is None:
        return ""
    return "".join(str(raw).split())


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "yes", "1"}


def _normalize_row(row) -> dict:
    """Map HF schema → notebook-friendly schema (idempotent).

    Accepts any mapping-like object (HuggingFace ``LazyRow`` is not a dict
    instance, so an ``isinstance(row, dict)`` guard would mis-fire).
    """
    refs = row.get("references") or []
    if not isinstance(refs, list):
        refs = [refs]
    doc_text = "\n\n".join(str(r) for r in refs)
    raw_text = row.get("text")
    return {
        # canonical
        "id": row.get("_id") or row.get("id"),
        "question": raw_text,
        "document_text": doc_text,
        "text": doc_text,  # alias — notebooks that index doc['text'] get the body
        "answer": row.get("answer"),
        "category": _normalize_category(row.get("category")),
        "reasoning_required": _coerce_bool(row.get("reasoning")),
        "type": row.get("type"),
        "references": refs,
        # preserved originals
        "_id": row.get("_id"),
        "_raw_text": raw_text,
    }


@lru_cache(maxsize=1)
def load(refresh: bool = False):
    """Download (and cache) FinDER. Returns a ``datasets.Dataset`` with the
    normalized schema documented in the module docstring."""
    from datasets import load_dataset, Dataset  # local import — heavy

    parquet_path = _cache_dir() / "finder_corpus.parquet"
    if parquet_path.exists() and not refresh:
        import pandas as pd
        df = pd.read_parquet(parquet_path)
        return Dataset.from_pandas(df, preserve_index=False)

    # HuggingFace call — repo, optional subset, split
    if DEFAULT_HF_SUBSET:
        ds = load_dataset(DEFAULT_HF_REPO, DEFAULT_HF_SUBSET, split=DEFAULT_HF_SPLIT)
    else:
        ds = load_dataset(DEFAULT_HF_REPO, split=DEFAULT_HF_SPLIT)

    ds = ds.map(_normalize_row)

    _cache_dir().mkdir(parents=True, exist_ok=True)
    ds.to_pandas().to_parquet(parquet_path, index=False)
    return ds


def by_category(name: str):
    """Filter to a single category (no-space form)."""
    target = _normalize_category(name)
    if target not in CATEGORIES:
        raise ValueError(
            f"unknown FinDER category {name!r}. Valid: {list(CATEGORIES)}"
        )
    return load().filter(lambda r: _normalize_category(r.get("category", "")) == target)


def sample_random(n: int, *, seed: int = 42):
    ds = load()
    rng = random.Random(seed)
    idxs = rng.sample(range(len(ds)), k=min(n, len(ds)))
    return ds.select(idxs)


def sample_per_category(
    n_per: int,
    *,
    seed: int = 42,
    categories: Optional[Iterable[str]] = None,
) -> List[dict]:
    """Balanced sample — ``n_per`` records per category. Returns ``list[dict]``."""
    out: List[dict] = []
    cats = list(categories) if categories else list(CATEGORIES)
    for cat in cats:
        try:
            sub = by_category(cat)
        except ValueError:
            continue
        rng = random.Random(seed)
        take = min(n_per, len(sub))
        idxs = rng.sample(range(len(sub)), k=take)
        out.extend(sub.select(idxs).to_list())
    return out


def category_distribution():
    """Per-category count + share. Returns a pandas DataFrame."""
    import pandas as pd

    ds = load()
    df = pd.DataFrame({"category": ds["category"]})
    counts = df["category"].value_counts().reset_index()
    counts.columns = ["category", "count"]
    counts["share"] = (counts["count"] / counts["count"].sum()).round(4)
    return counts.sort_values("count", ascending=False).reset_index(drop=True)


__all__ = [
    "CATEGORIES",
    "load",
    "by_category",
    "sample_random",
    "sample_per_category",
    "category_distribution",
]
