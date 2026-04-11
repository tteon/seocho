"""
SEOCHO Ontology Module — Bridge to canonical seocho.ontology.

The canonical ontology implementation lives in ``seocho/ontology.py``.
This module provides backward-compatible aliases so existing
``extraction/`` code continues to work without changes.

Usage (unchanged)::

    from extraction.ontology import Ontology
    ontology = Ontology.from_yaml("schema.yaml")
"""

from seocho.ontology import (
    ConstraintType,
    NodeDef as NodeDefinition,
    Ontology,
    P as PropertyDefinition,
    PropertyType,
    RelDef as RelationshipDefinition,
)

__all__ = [
    "Ontology",
    "NodeDefinition",
    "RelationshipDefinition",
    "PropertyDefinition",
    "PropertyType",
    "ConstraintType",
]
