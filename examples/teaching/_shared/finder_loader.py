"""FinDER loader for the teaching notebooks.

Resilient shim: prefers ``seocho.eval.benchmarks.finder`` when available
(SDK ≥ the version that closes seocho-ci24 / publishes 0.4.0). Falls back
to an inline implementation when running against an older PyPI release of
``seocho`` so the notebooks still work in Colab + ``pip install seocho``.

Either path returns records with the same **normalized** schema:

    id, question, document_text, text (alias of document_text),
    answer, category, reasoning_required, type, references, _id, _raw_text
"""

from __future__ import annotations

try:  # Preferred path — SDK already bundles the loader (≥ 0.4.0)
    from seocho.eval.benchmarks.finder import (  # type: ignore
        CATEGORIES as FINDER_CATEGORIES,
        by_category,
        category_distribution,
        load as load_finder,
        sample_per_category,
        sample_random,
    )

    __all__ = [
        "FINDER_CATEGORIES",
        "by_category",
        "category_distribution",
        "load_finder",
        "sample_per_category",
        "sample_random",
    ]

except ImportError:  # pragma: no cover — fallback for older pip seocho releases
    import os as _os
    import random as _random
    from functools import lru_cache as _lru_cache
    from pathlib import Path as _Path
    from typing import Iterable as _Iterable, List as _List, Optional as _Optional

    FINDER_CATEGORIES: tuple[str, ...] = (
        "Accounting",
        "CompanyOverview",
        "Financials",
        "Footnotes",
        "Governance",
        "Legal",
        "Risk",
        "ShareholderReturn",
    )

    _DEFAULT_HF_REPO = _os.getenv("FINDER_HF_REPO", "Linq-AI-Research/FinDER")
    _DEFAULT_HF_SUBSET = _os.getenv("FINDER_HF_SUBSET", "")
    _DEFAULT_HF_SPLIT = _os.getenv("FINDER_HF_SPLIT", "train")

    def _cache_dir() -> _Path:
        raw = _os.getenv("SEOCHO_DATASET_CACHE_DIR") or _os.getenv("FINDER_CACHE_DIR") or "./data"
        return _Path(raw)

    def _normalize_category(raw):
        if raw is None:
            return ""
        return "".join(str(raw).split())

    def _coerce_bool(value):
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"true", "yes", "1"}

    def _normalize_row(row) -> dict:
        refs = row.get("references") or []
        if not isinstance(refs, list):
            refs = [refs]
        doc_text = "\n\n".join(str(r) for r in refs)
        raw_text = row.get("text")
        return {
            "id": row.get("_id") or row.get("id"),
            "question": raw_text,
            "document_text": doc_text,
            "text": doc_text,
            "answer": row.get("answer"),
            "category": _normalize_category(row.get("category")),
            "reasoning_required": _coerce_bool(row.get("reasoning")),
            "type": row.get("type"),
            "references": refs,
            "_id": row.get("_id"),
            "_raw_text": raw_text,
        }

    @_lru_cache(maxsize=1)
    def load_finder(refresh: bool = False):
        from datasets import Dataset, load_dataset  # local import — heavy

        parquet_path = _cache_dir() / "finder_corpus.parquet"
        if parquet_path.exists() and not refresh:
            import pandas as pd
            df = pd.read_parquet(parquet_path)
            return Dataset.from_pandas(df, preserve_index=False)

        if _DEFAULT_HF_SUBSET:
            ds = load_dataset(_DEFAULT_HF_REPO, _DEFAULT_HF_SUBSET, split=_DEFAULT_HF_SPLIT)
        else:
            ds = load_dataset(_DEFAULT_HF_REPO, split=_DEFAULT_HF_SPLIT)

        ds = ds.map(_normalize_row)
        _cache_dir().mkdir(parents=True, exist_ok=True)
        ds.to_pandas().to_parquet(parquet_path, index=False)
        return ds

    def by_category(name: str):
        target = _normalize_category(name)
        if target not in FINDER_CATEGORIES:
            raise ValueError(f"unknown FinDER category {name!r}. Valid: {list(FINDER_CATEGORIES)}")
        return load_finder().filter(lambda r: _normalize_category(r.get("category", "")) == target)

    def sample_random(n: int, *, seed: int = 42):
        ds = load_finder()
        rng = _random.Random(seed)
        idxs = rng.sample(range(len(ds)), k=min(n, len(ds)))
        return ds.select(idxs)

    def sample_per_category(
        n_per: int,
        *,
        seed: int = 42,
        categories: _Optional[_Iterable[str]] = None,
    ) -> _List[dict]:
        out: _List[dict] = []
        cats = list(categories) if categories else list(FINDER_CATEGORIES)
        for cat in cats:
            try:
                sub = by_category(cat)
            except ValueError:
                continue
            rng = _random.Random(seed)
            take = min(n_per, len(sub))
            idxs = rng.sample(range(len(sub)), k=take)
            out.extend(sub.select(idxs).to_list())
        return out

    def category_distribution():
        import pandas as pd
        ds = load_finder()
        df = pd.DataFrame({"category": ds["category"]})
        counts = df["category"].value_counts().reset_index()
        counts.columns = ["category", "count"]
        counts["share"] = (counts["count"] / counts["count"].sum()).round(4)
        return counts.sort_values("count", ascending=False).reset_index(drop=True)

    __all__ = [
        "FINDER_CATEGORIES",
        "by_category",
        "category_distribution",
        "load_finder",
        "sample_per_category",
        "sample_random",
    ]
