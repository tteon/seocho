"""Regression for #130 — Ontology.merge must record relationship conflicts,
including cardinality, and raise on them in strict mode. Previously a
cardinality difference was never compared, so a strict merge silently kept the
left relationship's cardinality with no signal.
"""

from __future__ import annotations

import pytest

from seocho.ontology import NodeDef, Ontology, RelDef


def _onto(cardinality: str, *, source: str = "A", target: str = "B") -> Ontology:
    return Ontology(
        name="o",
        nodes={n: NodeDef(description=n) for n in ("A", "B", "C")},
        relationships={
            "R": RelDef(
                source=source, target=target,
                cardinality=cardinality, description="d",
            )
        },
    )


def test_cardinality_conflict_raises_in_strict_mode() -> None:
    left = _onto("ONE_TO_MANY")
    right = _onto("MANY_TO_MANY")
    with pytest.raises(ValueError, match="cardinality"):
        left.merge(right, strategy="strict")


def test_cardinality_conflict_is_silent_outside_strict() -> None:
    # union keeps the left cardinality (no raise) — behavior preserved.
    merged = _onto("ONE_TO_MANY").merge(_onto("MANY_TO_MANY"), strategy="union")
    assert merged.relationships["R"].cardinality == "ONE_TO_MANY"


def test_source_target_conflict_still_raises_in_strict_mode() -> None:
    left = _onto("ONE_TO_MANY", target="B")
    right = _onto("ONE_TO_MANY", target="C")
    with pytest.raises(ValueError, match="A->B vs A->C"):
        left.merge(right, strategy="strict")


def test_matching_relationships_merge_without_conflict() -> None:
    merged = _onto("ONE_TO_MANY").merge(_onto("ONE_TO_MANY"), strategy="strict")
    assert merged.relationships["R"].cardinality == "ONE_TO_MANY"
