"""Compose open-domain ontologies from module slices.

Open-domain analog of ``examples/finder/datasets/fibo_modules/compose.py`` for
the RAG-vs-GraphRAG replication (arXiv 2502.11371) on Wikipedia/news/novel
corpora, where FIBO does not apply. Modules form nested supersets so the
ontology size is the only moving variable across graph arms (CLAUDE.md §20.9):

    non-ontology   -> compose_modules([])            (Entity/RELATED_TO floor)
    generic-er     -> compose_modules(["ger"])       (Person/Org/Location/Work/Event)
    event-temporal -> compose_modules(["ger","evt"]) (adds TimePoint/Role + ordering)

``ger`` MUST precede ``evt`` when both are requested — ``evt`` references
``Event``/``Person`` defined in ``ger`` (no dangling relationship sources).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import yaml

from seocho import Ontology


_THIS_DIR = Path(__file__).resolve().parent

KNOWN_MODULES = ("ger", "evt")


def _baseline() -> Ontology:
    return Ontology.from_dict(
        {
            "graph_type": "baseline_generic",
            "package_id": "baseline_generic",
            "version": "1.0.0",
            "description": "Generic baseline — no open-domain module loaded",
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
        raise ValueError(f"Unknown open-domain module: {name}. Known: {KNOWN_MODULES}")
    path = _THIS_DIR / f"{name}.yaml"
    with open(path) as fh:
        data = yaml.safe_load(fh)
    return Ontology.from_dict(data)


def compose_modules(modules: Iterable[str]) -> Ontology:
    """Merge open-domain module slices into a single ``Ontology``.

    Empty list returns the generic baseline. ``ger`` is ordered first when
    present so ``evt``'s relationship sources resolve.
    """
    module_list: List[str] = list(modules)
    if not module_list:
        return _baseline()
    # Enforce ger-before-evt so evt's Event/Person sources resolve on merge.
    module_list = sorted(set(module_list), key=lambda m: KNOWN_MODULES.index(m))
    label = "+".join(module_list)
    composed = _load_module(module_list[0])
    composed.name = f"od_{label}"
    composed.package_id = f"od_{label}"
    composed.description = f"Open-domain {label.upper()} composition"
    for extra in module_list[1:]:
        composed = composed.merge(_load_module(extra))
        composed.name = f"od_{label}"
        composed.package_id = f"od_{label}"
    return composed
