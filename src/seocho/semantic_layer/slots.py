"""Resolved observation slots (ADR-0103, semantic layer).

`ObservationSlots` is the canonical, fully-resolved query target the arbiter and
the deterministic compiler share: every field is either a closed-vocab/canonical
value or recorded as unresolved. The compiler turns a fully-resolved instance
into exact-key Cypher; the arbiter inspects `unresolved` / `is_fully_resolved`
to route (STRUCTURED vs CLARIFY vs FAIL).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from .keys import observation_key


@dataclass(frozen=True, slots=True)
class ObservationSlots:
    entity_cik: str = ""
    concept_id: str = ""
    period_keys: Tuple[str, ...] = ()
    unit: str = "USD"
    basis: str = "consolidated"
    unresolved: Tuple[str, ...] = ()          # slot names that did not resolve

    @property
    def is_fully_resolved(self) -> bool:
        return bool(
            self.entity_cik and self.concept_id and self.period_keys
            and not self.unresolved
        )

    def observation_keys(self, *, workspace_id: str = "") -> Tuple[str, ...]:
        """Deterministic obs_id per requested period — the exact MERGE/match key."""
        return tuple(
            observation_key(
                entity_key=self.entity_cik,
                concept_id=self.concept_id,
                period_key=pk,
                unit=self.unit,
                basis=self.basis,
                workspace_id=workspace_id,
            )
            for pk in self.period_keys
        )
