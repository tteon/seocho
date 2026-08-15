"""Curated starter ontologies, shipped as package data.

``seocho ontology clone <template>`` hands the user a reviewed, working
schema instead of a blank page. Templates are ordinary native documents —
cloning returns a draft (never persists on its own), and the draft flows
into ``ontology check`` / ``create`` like any import.

Curation rule: a template only enters this registry once it has carried a
real workload in this repository — these are not speculative domain packs.
"""

from __future__ import annotations

from importlib import resources
from typing import Any, Dict, List

TEMPLATES: Dict[str, str] = {
    # name -> one-line description shown by `seocho ontology templates`
    "quickstart": "Minimal company/person/product schema (the seocho run quickstart).",
    "finance-aml": "FinBench-style AML graph: accounts, transfers, ownership, "
                   "channels — the schema behind the agent<->database study.",
    "finance-compliance": "Regulators, regulations, incidents, controls, policies "
                          "— the finance-compliance example's schema.",
}

_FILES = {
    "quickstart": "quickstart.yaml",
    "finance-aml": "finance_aml.yaml",
    "finance-compliance": "finance_compliance.yaml",
}


def list_templates() -> List[Dict[str, str]]:
    return [{"name": name, "description": description}
            for name, description in TEMPLATES.items()]


def load_template(name: str) -> Dict[str, Any]:
    """Return the template's native document. Raises KeyError on unknown names."""
    import yaml

    filename = _FILES.get(name)
    if filename is None:
        raise KeyError(
            f"unknown template {name!r}; available: {', '.join(sorted(TEMPLATES))}")
    content = resources.files(__package__).joinpath(filename).read_text("utf-8")
    return yaml.safe_load(content)


__all__ = ["TEMPLATES", "list_templates", "load_template"]
