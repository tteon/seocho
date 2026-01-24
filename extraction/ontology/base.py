"""
Ontology Base Module for SEOCHO

Provides abstract interfaces and data structures for ontology management.
Supports multiple formats: YAML, OWL, RDF.

Example:
    >>> from extraction.ontology import Ontology, NodeDefinition
    >>> ontology = Ontology.from_yaml("my_schema.yaml")
    >>> ontology.validate()
    >>> ontology.apply_to_neo4j(driver)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set
from enum import Enum
import yaml


class PropertyType(Enum):
    """Supported property types for Neo4j."""
    STRING = "STRING"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"
    DATETIME = "DATETIME"
    DATE = "DATE"
    POINT = "POINT"
    LIST = "LIST"


class ConstraintType(Enum):
    """Supported constraint types."""
    UNIQUE = "UNIQUE"
    NODE_KEY = "NODE_KEY"
    EXISTS = "EXISTS"


@dataclass
class PropertyDefinition:
    """Definition of a node/relationship property."""
    name: str
    type: PropertyType
    constraint: Optional[ConstraintType] = None
    index: bool = False
    required: bool = False
    description: str = ""
    
    def to_cypher_type(self) -> str:
        """Convert to Cypher type string."""
        type_map = {
            PropertyType.STRING: "STRING",
            PropertyType.INTEGER: "INTEGER",
            PropertyType.FLOAT: "FLOAT",
            PropertyType.BOOLEAN: "BOOLEAN",
            PropertyType.DATETIME: "DATETIME",
            PropertyType.DATE: "DATE",
            PropertyType.POINT: "POINT",
            PropertyType.LIST: "LIST",
        }
        return type_map.get(self.type, "STRING")


@dataclass
class NodeDefinition:
    """Definition of a graph node type."""
    label: str
    description: str = ""
    properties: Dict[str, PropertyDefinition] = field(default_factory=dict)
    
    @property
    def unique_properties(self) -> List[str]:
        """Get properties with UNIQUE constraint."""
        return [
            name for name, prop in self.properties.items()
            if prop.constraint == ConstraintType.UNIQUE
        ]
    
    @property
    def indexed_properties(self) -> List[str]:
        """Get properties with indexes."""
        return [
            name for name, prop in self.properties.items()
            if prop.index
        ]


@dataclass
class RelationshipDefinition:
    """Definition of a graph relationship type."""
    type: str
    source: str  # Source node label
    target: str  # Target node label
    description: str = ""
    properties: Dict[str, PropertyDefinition] = field(default_factory=dict)
    cardinality: str = "MANY_TO_MANY"  # ONE_TO_ONE, ONE_TO_MANY, MANY_TO_ONE, MANY_TO_MANY


@dataclass
class Ontology:
    """
    Complete ontology definition for a knowledge graph.
    
    Attributes:
        name: Ontology identifier
        version: Semantic version string
        nodes: Dictionary of node definitions
        relationships: Dictionary of relationship definitions
    """
    name: str
    version: str = "1.0.0"
    description: str = ""
    nodes: Dict[str, NodeDefinition] = field(default_factory=dict)
    relationships: Dict[str, RelationshipDefinition] = field(default_factory=dict)
    
    # Allowed labels for security (Cypher injection prevention)
    _allowed_labels: Set[str] = field(default_factory=set)
    
    def __post_init__(self):
        """Initialize allowed labels from node definitions."""
        self._allowed_labels = set(self.nodes.keys())
    
    @classmethod
    def from_yaml(cls, yaml_path: str) -> 'Ontology':
        """
        Load ontology from YAML file.
        
        Args:
            yaml_path: Path to YAML schema file
            
        Returns:
            Ontology: Parsed ontology object
        """
        with open(yaml_path, 'r') as f:
            data = yaml.safe_load(f)
        
        nodes = {}
        for label, node_data in data.get('nodes', {}).items():
            properties = {}
            for prop_name, prop_data in node_data.get('properties', {}).items():
                prop_type = PropertyType[prop_data.get('type', 'STRING').upper()]
                constraint = None
                if 'constraint' in prop_data:
                    constraint = ConstraintType[prop_data['constraint'].upper()]
                
                properties[prop_name] = PropertyDefinition(
                    name=prop_name,
                    type=prop_type,
                    constraint=constraint,
                    index=prop_data.get('index', False),
                    required=prop_data.get('required', False),
                    description=prop_data.get('description', '')
                )
            
            nodes[label] = NodeDefinition(
                label=label,
                description=node_data.get('description', ''),
                properties=properties
            )
        
        relationships = {}
        for rel_type, rel_data in data.get('relationships', {}).items():
            properties = {}
            for prop_name, prop_data in rel_data.get('properties', {}).items():
                prop_type = PropertyType[prop_data.get('type', 'STRING').upper()]
                properties[prop_name] = PropertyDefinition(
                    name=prop_name,
                    type=prop_type,
                    description=prop_data.get('description', '')
                )
            
            relationships[rel_type] = RelationshipDefinition(
                type=rel_type,
                source=rel_data.get('source', 'Any'),
                target=rel_data.get('target', 'Any'),
                description=rel_data.get('description', ''),
                properties=properties,
                cardinality=rel_data.get('cardinality', 'MANY_TO_MANY')
            )
        
        return cls(
            name=data.get('graph_type', 'Unnamed'),
            version=data.get('version', '1.0.0'),
            description=data.get('description', ''),
            nodes=nodes,
            relationships=relationships
        )
    
    def to_yaml(self, yaml_path: str) -> None:
        """Export ontology to YAML file."""
        data = {
            'graph_type': self.name,
            'version': self.version,
            'description': self.description,
            'nodes': {},
            'relationships': {}
        }
        
        for label, node in self.nodes.items():
            data['nodes'][label] = {
                'description': node.description,
                'properties': {
                    prop.name: {
                        'type': prop.type.value,
                        'constraint': prop.constraint.value if prop.constraint else None,
                        'index': prop.index,
                        'required': prop.required
                    }
                    for prop in node.properties.values()
                }
            }
        
        for rel_type, rel in self.relationships.items():
            data['relationships'][rel_type] = {
                'source': rel.source,
                'target': rel.target,
                'description': rel.description,
                'cardinality': rel.cardinality
            }
        
        with open(yaml_path, 'w') as f:
            yaml.dump(data, f, sort_keys=False, default_flow_style=False)
    
    def is_valid_label(self, label: str) -> bool:
        """Check if a label is allowed (security check)."""
        return label in self._allowed_labels or label == "Entity"
    
    def sanitize_label(self, label: str) -> str:
        """
        Sanitize a label for safe Cypher execution.
        
        Args:
            label: Raw label from extraction
            
        Returns:
            str: Safe label (falls back to 'Entity' if not in ontology)
        """
        if self.is_valid_label(label):
            return label
        return "Entity"
    
    def validate(self) -> List[str]:
        """
        Validate ontology consistency.
        
        Returns:
            List[str]: List of validation errors (empty if valid)
        """
        errors = []
        
        # Check relationship source/target exist
        for rel_type, rel in self.relationships.items():
            if rel.source != "Any" and rel.source not in self.nodes:
                errors.append(f"Relationship '{rel_type}' references unknown source node '{rel.source}'")
            if rel.target != "Any" and rel.target not in self.nodes:
                errors.append(f"Relationship '{rel_type}' references unknown target node '{rel.target}'")
        
        # Check for nodes without any unique constraint
        for label, node in self.nodes.items():
            if not node.unique_properties:
                errors.append(f"Node '{label}' has no UNIQUE constraint - consider adding one for 'id' or 'name'")
        
        return errors
    
    def generate_cypher_constraints(self) -> List[str]:
        """
        Generate Cypher statements for constraints and indexes.
        
        Returns:
            List[str]: Cypher statements to create constraints/indexes
        """
        statements = []
        
        for label, node in self.nodes.items():
            # Unique constraints
            for prop_name in node.unique_properties:
                constraint_name = f"constraint_{label}_{prop_name}_unique"
                statements.append(
                    f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.{prop_name} IS UNIQUE"
                )
            
            # Indexes
            for prop_name in node.indexed_properties:
                if prop_name not in node.unique_properties:  # UNIQUE already creates index
                    index_name = f"index_{label}_{prop_name}"
                    statements.append(
                        f"CREATE INDEX {index_name} IF NOT EXISTS "
                        f"FOR (n:{label}) ON (n.{prop_name})"
                    )
        
        return statements
    
    def apply_to_neo4j(self, driver, database: str = "neo4j") -> Dict[str, Any]:
        """
        Apply ontology constraints and indexes to Neo4j.
        
        Args:
            driver: Neo4j driver instance
            database: Target database name
            
        Returns:
            Dict with 'success' count and 'errors' list
        """
        statements = self.generate_cypher_constraints()
        results = {"success": 0, "errors": []}
        
        with driver.session(database=database) as session:
            for stmt in statements:
                try:
                    session.run(stmt)
                    results["success"] += 1
                except Exception as e:
                    results["errors"].append(f"{stmt}: {str(e)}")
        
        return results
    
    def __repr__(self) -> str:
        return f"Ontology(name='{self.name}', nodes={len(self.nodes)}, relationships={len(self.relationships)})"
