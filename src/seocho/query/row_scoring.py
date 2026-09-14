"""Shared financial row ranking; callers retain company matching policy."""

from __future__ import annotations
from typing import Any, Dict, Sequence


def row_match_score(
    row: Dict[str, Any],
    metric_aliases: Sequence[str],
    scope_tokens: Sequence[str],
    *,
    company_score: int,
) -> int:
    score = company_score
    metric_text = str(row.get("metric_name", "")).lower()
    score += sum(3 for token in scope_tokens if token in metric_text)
    score += sum(1 for alias in metric_aliases if alias in metric_text)
    if str(row.get("relationship", "")) in {"REPORTED", "reported"}:
        score += 2
    return score
