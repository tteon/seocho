"""Regression tests for FinDER category normalization (ISSUE-4).

Pins the contract that ``by_category`` and ``sample_per_category`` work for
ALL eight categories, including the two multi-word ones
(``CompanyOverview``, ``ShareholderReturn``) that were silently dropped
because ``_normalize_category`` removed spaces without normalizing case.

These require network access to HuggingFace (or a local FinDER cache);
they are skipped automatically when the dataset can't be loaded.
"""

from __future__ import annotations

import pytest

from seocho.eval.benchmarks.finder import (
    CATEGORIES,
    _normalize_category,
    by_category,
    sample_per_category,
)


# --- pure-function tests (no network) -------------------------------------

def test_normalize_category_produces_declared_category_form():
    """Raw HF labels must normalize to the exact strings in CATEGORIES."""
    assert _normalize_category("Company overview") == "CompanyOverview"
    assert _normalize_category("Shareholder return") == "ShareholderReturn"
    assert _normalize_category("Financials") == "Financials"
    assert _normalize_category("Footnotes") == "Footnotes"


def test_normalize_category_is_idempotent():
    """Normalizing an already-normalized value must be a no-op."""
    for cat in CATEGORIES:
        assert _normalize_category(cat) == cat


def test_every_normalized_form_is_in_CATEGORIES():
    raw_labels = [
        "Accounting", "Company overview", "Financials", "Footnotes",
        "Governance", "Legal", "Risk", "Shareholder return",
    ]
    for raw in raw_labels:
        assert _normalize_category(raw) in CATEGORIES, raw


# --- dataset-backed tests (network / cache) -------------------------------

def _dataset_available() -> bool:
    try:
        from seocho.eval.benchmarks.finder import load
        load()
        return True
    except Exception:
        return False


pytestmark_dataset = pytest.mark.skipif(
    not _dataset_available(),
    reason="FinDER dataset not available (no HF token / cache)",
)


@pytestmark_dataset
def test_by_category_returns_rows_for_every_category():
    """Each declared category must resolve to a non-empty slice.

    Before the fix, CompanyOverview and ShareholderReturn returned 0 rows.
    """
    empty = []
    for cat in CATEGORIES:
        n = len(by_category(cat))
        if n == 0:
            empty.append(cat)
    assert not empty, f"categories resolved to 0 rows: {empty}"


@pytestmark_dataset
def test_sample_per_category_covers_all_categories():
    """Balanced sampler must include every category, not silently drop some."""
    rows = sample_per_category(10)
    seen = {r["category"] for r in rows}
    missing = set(CATEGORIES) - seen
    assert not missing, f"balanced sample missing categories: {missing}"


@pytestmark_dataset
def test_observed_categories_match_declared_set():
    """Hybrid guard: the data's normalized categories must equal CATEGORIES.

    Detects code/data drift (renamed/added category, normalization regression)
    that would otherwise only show up as empty slices downstream.
    """
    from seocho.eval.benchmarks.finder import load

    observed = {r["category"] for r in load()}
    assert observed == set(CATEGORIES), (
        f"category drift: unexpected={observed - set(CATEGORIES)}, "
        f"missing={set(CATEGORIES) - observed}"
    )
