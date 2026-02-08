"""
Ontology Prompt Bridge

Converts ontology definitions (NodeDefinition, RelationshipDefinition)
into LLM-friendly prompt fragments so extraction is driven by the
ontology rather than hard-coded entity types.
"""

import logging
from typing import Dict

from ontology.base import Ontology

logger = logging.getLogger(__name__)


class OntologyPromptBridge:
    """Bridge between Ontology definitions and LLM extraction prompts."""

    def __init__(self, ontology: Ontology):
        self.ontology = ontology

    def get_entity_types_prompt(self) -> str:
        """Convert all node definitions into a prompt-friendly listing.

        Example output line:
            - Organization: Company or institution (properties: name[UNIQUE], founded_year)
        """
        lines = []
        for label, node in self.ontology.nodes.items():
            props = ", ".join(
                f"{p.name}[{p.constraint.value}]" if p.constraint else p.name
                for p in node.properties.values()
            )
            desc = node.description or label
            lines.append(f"- {label}: {desc} (properties: {props})")
        return "\n".join(lines)

    def get_relationship_types_prompt(self) -> str:
        """Convert relationship definitions into a prompt-friendly listing.

        Example output line:
            - WORKS_AT: Person -> Organization (employment relationship)
        """
        lines = []
        for rel_type, rel in self.ontology.relationships.items():
            desc = rel.description or rel_type
            lines.append(f"- {rel_type}: {rel.source} -> {rel.target} ({desc})")
        return "\n".join(lines)

    def render_extraction_context(self) -> Dict[str, str]:
        """Return a context dict suitable for PromptManager template rendering."""
        return {
            "entity_types": self.get_entity_types_prompt(),
            "relationship_types": self.get_relationship_types_prompt(),
            "ontology_name": self.ontology.name,
        }
