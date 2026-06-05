"""Compose ontologies from decision-making (email) module slices.

Mirrors ``examples/finder/datasets/fibo_modules/compose.py`` for the email
decision-making domain. ``email_core`` is the shared anchor (all relationships
in ``decision_entities`` / ``argument_stance`` source/target its Person /
EmailThread / Proposal nodes), so the ontology-size arms are nested supersets:

    non-ontology  -> compose_modules([])                       (Entity/RELATED_TO)
    core          -> ["email_core"]                            (who/when/threads)
    decision      -> ["email_core", "decision_entities"]       (+proposals/decisions)
    argument      -> ["email_core", "decision_entities", "argument_stance"]  (+stance/why)

The empty-config baseline (``compose_modules([])``) returns the generic
Entity/RELATED_TO schema — the no-ontology reference point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List

import yaml

from seocho import Ontology

_THIS_DIR = Path(__file__).resolve().parent

KNOWN_MODULES = ("email_core", "decision_entities", "argument_stance")

# Nested-superset arms (email_core is the anchor; never a dangling rel source).
ARMS: Dict[str, List[str]] = {
    "non-ontology": [],
    "core": ["email_core"],
    "decision": ["email_core", "decision_entities"],
    "argument": ["email_core", "decision_entities", "argument_stance"],
}


def _baseline() -> Ontology:
    return Ontology.from_dict(
        {
            "graph_type": "baseline_generic",
            "package_id": "baseline_generic",
            "version": "1.0.0",
            "description": "Generic baseline — no decision ontology loaded",
            "graph_model": "lpg",
            "nodes": {
                "Entity": {
                    "description": "Generic named entity",
                    "properties": {
                        "name": {"type": "STRING", "constraint": "UNIQUE", "required": True},
                    },
                },
            },
            "relationships": {
                "RELATED_TO": {
                    "source": "Entity",
                    "target": "Entity",
                    "description": "Generic relationship",
                    "cardinality": "MANY_TO_MANY",
                },
            },
        }
    )


def _load_module(name: str) -> Ontology:
    if name not in KNOWN_MODULES:
        raise ValueError(f"Unknown decision module: {name}. Known: {KNOWN_MODULES}")
    path = _THIS_DIR / f"{name}.yaml"
    with open(path) as fh:
        data = yaml.safe_load(fh)
    return Ontology.from_dict(data)


def compose_modules(modules: Iterable[str]) -> Ontology:
    """Merge decision module slices into one Ontology. Empty list → baseline.
    ``Ontology.merge`` is symmetric on labels/rel types, so order is irrelevant."""
    module_list: List[str] = list(modules)
    if not module_list:
        return _baseline()
    label = "+".join(module_list)
    composed = _load_module(module_list[0])
    for extra in module_list[1:]:
        composed = composed.merge(_load_module(extra))
    composed.name = f"decision_{label}"
    composed.package_id = f"decision_{label}"
    composed.description = f"Decision-making composition: {label}"
    return composed
