"""A small upper (foundational) ontology — the abstract interface for cold-start.

hadry's design principle: *domain-driven interfaces let you think abstractly,
without hyperfixating on specific dataset quirks.* At cold-start there is no domain
ontology, and flat open extraction lets the LLM invent a different type name for the
same concept in every chunk (vocabulary drift) — which fragments the graph and
starves axiom induction of support.

The fix is to design against an ABSTRACT interface, not the dataset's concrete types:
a small, domain-independent set of foundational categories + abstract relations. The
LLM extracts CONCRETE entities but anchors each to an upper category
(``{type:"Company", upper:"Organization"}``). The upper anchor is stable and shared,
so synonyms cluster under one category — the clean grouping axis for post-pass
induction (``induce_ontology_from_graph``). Kept deliberately small and abstract so it
is a *soft frame*, not a rich closed vocabulary that would hurt recall (the
"Anchor, Don't Name" / extraction-firewall lesson).

See wiki/cold-start-schema-bootstrap-design.md.
"""

from __future__ import annotations

from typing import Dict, List

from .core import NodeDef, Ontology, RelDef

# Foundational categories (a lightweight schema.org-top / gist-flavored set — not the
# full DOLCE/BFO, which would be too heavy for an extraction frame). A tiny internal
# hierarchy (Person/Organization ⊑ Agent) demonstrates the anchor's grouping power.
UPPER_NODES: Dict[str, NodeDef] = {
    "Agent": NodeDef(description="Anything that can act or bear responsibility."),
    "Organization": NodeDef(description="A structured group (company, body, team).",
                            broader=["Agent"]),
    "Person": NodeDef(description="An individual human.", broader=["Agent"]),
    "Artifact": NodeDef(description="A made or managed object (product, system, control)."),
    "Event": NodeDef(description="Something that happens in time (incident, meeting, filing)."),
    "Concept": NodeDef(description="An abstract notion, rule, category, or topic."),
    "Location": NodeDef(description="A place or jurisdiction."),
    "TimeInterval": NodeDef(description="A date, period, or duration."),
    "Claim": NodeDef(description="A statement, assertion, or piece of evidence."),
    "Quantity": NodeDef(description="A measured or numeric value."),
    "Document": NodeDef(description="A record or source text."),
}

# Abstract relation kinds. Source/target are the typical upper categories — a frame,
# not a hard constraint.
UPPER_RELATIONS: Dict[str, RelDef] = {
    "partOf": RelDef(source="Concept", target="Concept",
                     description="mereological containment (X is part of Y)."),
    "participatesIn": RelDef(source="Agent", target="Event",
                             description="an agent takes part in an event."),
    "causes": RelDef(source="Event", target="Event",
                     description="X brings about / leads to Y."),
    "attributedTo": RelDef(source="Claim", target="Agent",
                           description="a claim/artifact is attributed to an agent."),
    "locatedIn": RelDef(source="Agent", target="Location",
                        description="X is located in / under jurisdiction Y."),
    "governs": RelDef(source="Concept", target="Agent",
                      description="a rule/concept governs / applies to an agent."),
    "precedes": RelDef(source="Event", target="Event",
                       description="temporal ordering (X before Y)."),
    "hasValue": RelDef(source="Concept", target="Quantity",
                       description="X has a measured value Y."),
}

UPPER_CATEGORIES: List[str] = list(UPPER_NODES.keys())
UPPER_RELATION_NAMES: List[str] = list(UPPER_RELATIONS.keys())


def build_upper_ontology() -> Ontology:
    """The foundational ontology used as the cold-start extraction frame."""
    return Ontology(
        name="seocho_upper",
        version="1.0.0",
        description="Foundational categories + abstract relations for cold-start.",
        nodes=dict(UPPER_NODES),
        relationships=dict(UPPER_RELATIONS),
    )


def render_upper_frame() -> str:
    """A compact extraction-prompt frame: abstract categories + relations + the
    anchoring instruction. Small and soft on purpose."""
    cats = "\n".join(f"  - {k}: {v.description}" for k, v in UPPER_NODES.items())
    rels = "\n".join(f"  - {k}: {v.description}" for k, v in UPPER_RELATIONS.items())
    return (
        "You are extracting a knowledge graph WITHOUT a fixed domain schema.\n"
        "Use these ABSTRACT foundational categories as a frame — do not use them as\n"
        "the entity labels themselves. Instead, give each entity a SPECIFIC type name\n"
        "of your choosing, and ANCHOR it to exactly one foundational category via an\n"
        "`upper` field. Reuse specific type names consistently across the document.\n\n"
        f"Foundational categories:\n{cats}\n\n"
        f"Abstract relation kinds (map your specific relations to the closest one via `upper`):\n{rels}\n\n"
        "Example entity: {\"id\": \"acme_corp\", \"type\": \"Company\", "
        "\"upper\": \"Organization\", \"properties\": {\"name\": \"Acme Corp\"}}\n"
        "Prefer a specific type; introduce new specific types freely when none fits."
    )
