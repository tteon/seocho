"""SEOCHO semantic layer (ADR-0103).

The single source of truth that the extraction writer and the query reader both
import, so metric identity cannot drift: a closed metric-concept taxonomy
(`ConceptRegistry`), a deterministic Observation key (`observation_key`), a
canonical period model (`normalize_period` / `Period`), entity→CIK resolution
(`EntityResolver`), and the resolved slot shape (`ObservationSlots`).
"""

from __future__ import annotations

from .concepts import (
    ConceptRegistry,
    MetricConcept,
    DEFAULT_FINANCE_CONCEPTS,
    default_registry,
)
from .identity import EntityResolver, default_resolver, normalize_name
from .keys import observation_key
from .periods import Period, normalize_period, parse_period
from .slots import ObservationSlots

__all__ = [
    "ConceptRegistry",
    "MetricConcept",
    "DEFAULT_FINANCE_CONCEPTS",
    "default_registry",
    "EntityResolver",
    "default_resolver",
    "normalize_name",
    "observation_key",
    "Period",
    "normalize_period",
    "parse_period",
    "ObservationSlots",
]
