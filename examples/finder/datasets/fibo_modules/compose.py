"""Compose ontologies from FIBO module slices.

Each slice in this directory (``be.yaml``, ``fbc.yaml``, ``sec.yaml``,
``fnd.yaml``, ``ind.yaml``) defines a small piece of the FIBO universe.
``compose_modules`` merges any subset of slices into one ``Ontology`` so
the FinDER FIBO-impact tutorial can sweep across module configurations
without hand-maintaining N copies of the schema.

The empty-config baseline (``compose_modules([])``) returns a generic
schema with no FIBO labels — it's the reference point for measuring
FIBO impact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import yaml

from seocho import Ontology


_THIS_DIR = Path(__file__).resolve().parent

KNOWN_MODULES = ("be", "fbc", "sec", "fnd", "ind")


def _baseline() -> Ontology:
    return Ontology.from_dict(
        {
            "graph_type": "baseline_generic",
            "package_id": "baseline_generic",
            "version": "1.0.0",
            "description": "Generic baseline — no FIBO module loaded",
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
        raise ValueError(f"Unknown FIBO module: {name}. Known: {KNOWN_MODULES}")
    path = _THIS_DIR / f"{name}.yaml"
    with open(path) as fh:
        data = yaml.safe_load(fh)
    return Ontology.from_dict(data)


def compose_modules(modules: Iterable[str]) -> Ontology:
    """Merge a list of FIBO module slices into a single ``Ontology``.

    Empty list returns the generic baseline. Order doesn't matter
    because ``Ontology.merge`` is symmetric on labels and rel types.
    """
    module_list: List[str] = list(modules)
    if not module_list:
        return _baseline()
    label = "+".join(module_list)
    composed = _load_module(module_list[0])
    composed.name = f"fibo_{label}"
    composed.package_id = f"fibo_{label}"
    composed.description = f"FIBO {label.upper()} composition"
    for extra in module_list[1:]:
        composed = composed.merge(_load_module(extra))
        composed.name = f"fibo_{label}"
        composed.package_id = f"fibo_{label}"
    return composed
