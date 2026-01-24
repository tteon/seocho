"""
SEOCHO Ontology Module

Provides ontology management for knowledge graph schema definition.

Supported Formats:
- YAML (native)
- OWL/RDF (via loaders)

Usage:
    from extraction.ontology import Ontology
    
    # Load from YAML
    ontology = Ontology.from_yaml("schema.yaml")
    
    # Validate
    errors = ontology.validate()
    
    # Apply to Neo4j
    ontology.apply_to_neo4j(driver, database="kgnormal")
"""

from .base import (
    Ontology,
    NodeDefinition,
    RelationshipDefinition,
    PropertyDefinition,
    PropertyType,
    ConstraintType,
)

__all__ = [
    "Ontology",
    "NodeDefinition",
    "RelationshipDefinition",
    "PropertyDefinition",
    "PropertyType",
    "ConstraintType",
]
