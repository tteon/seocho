"""
Backward-compatible bridge — canonical implementation is in seocho.ontology.

All classes are re-exported from the SDK package. Existing code that does
``from ontology.base import Ontology`` continues to work.
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
