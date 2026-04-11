"""
Ontology Prompt Bridge

Converts ontology definitions into LLM-friendly prompt fragments so
extraction is driven by the ontology rather than hard-coded entity types.

Uses the canonical ``seocho.ontology.Ontology`` as its source. This module
is a thin wrapper — the heavy lifting is in ``Ontology.to_extraction_context()``.
For new code, prefer calling ``ontology.to_extraction_context()`` directly.
"""

import logging
from typing import Dict

from seocho.ontology import Ontology

logger = logging.getLogger(__name__)


class OntologyPromptBridge:
    """Bridge between Ontology definitions and LLM extraction prompts."""

    def __init__(self, ontology: Ontology):
        self.ontology = ontology

    def get_entity_types_prompt(self) -> str:
        """Convert all node definitions into a prompt-friendly listing."""
        lines = []
        for label, node in self.ontology.nodes.items():
            props = ", ".join(
                f"{pname}[{p.constraint.value}]" if p.constraint else pname
                for pname, p in node.properties.items()
            )
            desc = node.description or label
            lines.append(f"- {label}: {desc} (properties: {props})")
        return "\n".join(lines)

    def get_relationship_types_prompt(self) -> str:
        """Convert relationship definitions into a prompt-friendly listing."""
        lines = []
        for rel_type, rel in self.ontology.relationships.items():
            desc = rel.description or rel_type
            lines.append(f"- {rel_type}: {rel.source} -> {rel.target} ({desc})")
        return "\n".join(lines)

    def render_extraction_context(self) -> Dict[str, str]:
        """Return a context dict suitable for PromptManager template rendering.

        This delegates to ``Ontology.to_extraction_context()`` for consistency,
        falling back to the manual rendering above for backward compatibility.
        """
        return self.ontology.to_extraction_context()
