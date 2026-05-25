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

import logging
import os
import random
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)


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


def _category_key(value: str) -> str:
    """Collapse a category label to a casing/spacing-insensitive lookup key."""
    return "".join(str(value).split()).lower()


# Reverse index derived from CATEGORIES so the normalizer and the declared
# category set can never drift apart (single source of truth). Any raw label
# whose key matches a canonical category resolves to that canonical spelling.
_CANONICAL_BY_KEY: dict[str, str] = {_category_key(c): c for c in CATEGORIES}

DEFAULT_HF_REPO = os.getenv("FINDER_HF_REPO", "Linq-AI-Research/FinDER")
DEFAULT_HF_SUBSET = os.getenv("FINDER_HF_SUBSET", "")  # no subset
DEFAULT_HF_SPLIT = os.getenv("FINDER_HF_SPLIT", "train")


def _cache_dir() -> Path:
    raw = os.getenv("SEOCHO_DATASET_CACHE_DIR") or os.getenv("FINDER_CACHE_DIR") or "./data"
    return Path(raw)


def _normalize_category(raw: Optional[str]) -> str:
    """Normalize a raw FinDER category to its declared :data:`CATEGORIES` form.

    Resolution is casing- and spacing-insensitive via :data:`_CANONICAL_BY_KEY`,
    which is derived from :data:`CATEGORIES`, so any known label (``"Company
    overview"``, ``"COMPANY OVERVIEW"``, already-normalized ``"CompanyOverview"``)
    maps to the single declared spelling and the two can never drift apart.
    Unknown labels fall back to a title-cased, space-stripped form rather than
    failing, preserving forward compatibility if FinDER adds a category.
    """
    if raw is None:
        return ""
    canonical = _CANONICAL_BY_KEY.get(_category_key(raw))
    if canonical is not None:
        return canonical
    return "".join(word[:1].upper() + word[1:] for word in str(raw).split())


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


def _verify_categories(ds) -> None:
    """Cross-check the dataset's observed categories against :data:`CATEGORIES`.

    The declared :data:`CATEGORIES` set is the contract; this compares it
    against what the data actually contains so a drift (a renamed/added/typo'd
    FinDER category, or a normalization regression) surfaces as a warning
    instead of silently producing empty ``by_category`` slices. Loading is not
    aborted. A benchmark loader should degrade loudly, not crash.
    """
    try:
        observed = {r["category"] for r in ds}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("FinDER category verification skipped: %s", exc)
        return
    unexpected = observed - set(CATEGORIES)
    missing = set(CATEGORIES) - observed
    if unexpected:
        logger.warning(
            "FinDER categories present in data but not in CATEGORIES: %s",
            sorted(unexpected),
        )
    if missing:
        logger.warning(
            "FinDER categories declared in CATEGORIES but absent from data: %s",
            sorted(missing),
        )


@lru_cache(maxsize=1)
def load(refresh: bool = False):
    """Download (and cache) FinDER. Returns a ``datasets.Dataset`` with the
    normalized schema documented in the module docstring."""
    from datasets import load_dataset, Dataset  # local import — heavy

    # Cache filename carries a schema version; bump it whenever the
    # normalized schema changes so stale caches are not silently reused.
    # v2: category normalization now produces declared CATEGORIES form.
    parquet_path = _cache_dir() / "finder_corpus_v2.parquet"
    if parquet_path.exists() and not refresh:
        import pandas as pd
        df = pd.read_parquet(parquet_path)
        ds = Dataset.from_pandas(df, preserve_index=False)
        _verify_categories(ds)
        return ds

    # HuggingFace call — repo, optional subset, split
    if DEFAULT_HF_SUBSET:
        ds = load_dataset(DEFAULT_HF_REPO, DEFAULT_HF_SUBSET, split=DEFAULT_HF_SPLIT)
    else:
        ds = load_dataset(DEFAULT_HF_REPO, split=DEFAULT_HF_SPLIT)

    # Force a fresh map on a parquet cache miss: HuggingFace fingerprints
    # ``.map`` by the mapped function's bytecode, which does not change when
    # a transitive dependency like ``_normalize_category`` is edited. Reusing
    # the stale arrow cache would silently re-emit the old normalized schema.
    ds = ds.map(_normalize_row, load_from_cache_file=False)

    _cache_dir().mkdir(parents=True, exist_ok=True)
    ds.to_pandas().to_parquet(parquet_path, index=False)
    _verify_categories(ds)
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
