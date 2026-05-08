"""Compatibility shim — TTL I/O and plus/minus now live in seocho core.

The original tutorial helpers were promoted to first-class methods on
``seocho.Ontology``:

    Ontology.from_ttl(path)        # was examples.ontology_io.ontology_from_ttl
    Ontology.to_ttl(path)          # was examples.ontology_io.ontology_to_ttl
    Ontology.subtract(other)       # was examples.ontology_io.ontology_minus
    Ontology + Ontology            # union merge (was ontology_plus)
    Ontology - Ontology            # subtract       (was ontology_minus)

This module remains so older notebook copies that still import from
``examples.ontology_io`` keep working. New code should import from
``seocho`` directly.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

from seocho import Ontology
from seocho.ontology_serialization import (
    ontology_from_ttl as _ontology_from_ttl,
    ontology_subtract as _ontology_subtract,
    ontology_to_ttl as _ontology_to_ttl,
)


def ontology_from_ttl(path, *, name=None, namespace=None) -> Ontology:
    return _ontology_from_ttl(Ontology, path, name=name, namespace=namespace)


def ontology_to_ttl(ontology: Ontology, path) -> Path:
    return _ontology_to_ttl(ontology, path)


def ontology_plus(*ontologies: Ontology, strategy: str = "union") -> Ontology:
    if not ontologies:
        raise ValueError("ontology_plus needs at least one ontology")
    composed = deepcopy(ontologies[0])
    for extra in ontologies[1:]:
        composed = composed.merge(extra, strategy=strategy)
    return composed


def ontology_minus(left: Ontology, right: Ontology) -> Ontology:
    return _ontology_subtract(left, right)


def labels_diff(a: Ontology, b: Ontology) -> Dict[str, List[str]]:
    """Quick set diff for human inspection of compositions."""
    a_labels, b_labels = set(a.nodes.keys()), set(b.nodes.keys())
    a_rels, b_rels = set(a.relationships.keys()), set(b.relationships.keys())
    return {
        "labels_only_in_a": sorted(a_labels - b_labels),
        "labels_only_in_b": sorted(b_labels - a_labels),
        "labels_in_both": sorted(a_labels & b_labels),
        "rels_only_in_a": sorted(a_rels - b_rels),
        "rels_only_in_b": sorted(b_rels - a_rels),
    }
