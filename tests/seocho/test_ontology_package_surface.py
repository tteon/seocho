"""The ontology package must not have changed anything a caller can see.

Sixteen modules and 7,872 lines moved from the flat `src/seocho/` root into
`src/seocho/ontology/`. The move is only worth doing if it is invisible: every
name that resolved before must still resolve, by the same path, to the same
object. These tests are the proof, and they are what fails if someone later
"tidies up" a shim.
"""

from __future__ import annotations

import importlib

import pytest

_FLAT_TO_CANONICAL = {
    "ontology_ambiguity": "ambiguity",
    "ontology_artifacts": "artifacts",
    "ontology_context": "context",
    "ontology_context_map": "context_map",
    "ontology_control_plane": "control_plane",
    "ontology_governance": "governance",
    "ontology_import": "importers",
    "ontology_ontoclean": "ontoclean",
    "ontology_resync": "resync",
    "ontology_run_context": "run_context",
    "ontology_scorecard": "scorecard",
    "ontology_serialization": "serialization",
    "ontology_slice": "slice",
    "ontology_snapshot_store": "snapshot_store",
    "ontology_versioning": "versioning",
}


@pytest.mark.parametrize("flat,canonical", sorted(_FLAT_TO_CANONICAL.items()))
def test_flat_path_is_the_same_object_as_the_canonical_one(flat, canonical):
    """`is`, not merely equivalent.

    A shim that forwards attribute reads but keeps its own module object looks
    correct until someone calls `monkeypatch.setattr` against the flat path:
    the patch lands on the shim while the code under test keeps calling the
    original. That is exactly how the first version of this move broke
    test_ontology_reasoner, so the identity is pinned rather than the surface.
    """
    assert importlib.import_module(f"seocho.{flat}") is importlib.import_module(
        f"seocho.ontology.{canonical}"
    )


def test_the_headline_import_still_works():
    from seocho.ontology import Ontology

    assert Ontology.__name__ == "Ontology"


def test_the_core_type_is_reachable_from_the_top_level_facade():
    """`from seocho import Ontology` is the documented entry point."""
    import seocho

    from seocho.ontology import Ontology as FromPackage

    assert seocho.Ontology is FromPackage


def test_every_declared_export_resolves():
    """The package surface is declared in _EXPORTS; a stale entry must fail here."""
    import seocho.ontology as pkg

    unresolved = []
    for name in pkg.__all__:
        try:
            getattr(pkg, name)
        except AttributeError:
            unresolved.append(name)
    assert not unresolved, f"declared but unreachable: {unresolved}"


def test_submodules_are_reachable_as_attributes():
    import seocho.ontology as pkg

    assert pkg.governance is importlib.import_module("seocho.ontology.governance")


def test_an_unknown_name_still_raises_attribute_error():
    """The lazy __getattr__ must not turn typos into ImportErrors or None."""
    import seocho.ontology as pkg

    with pytest.raises(AttributeError):
        pkg.NoSuchOntologyThing


def test_the_package_import_does_not_eagerly_load_every_submodule():
    """Laziness is load-bearing: core and serialization import each other.

    Those cycles predate the move and survive because the imports sit inside
    methods. An eager __init__ would turn them into an ImportError at first
    touch, so this asserts the package root stays cheap.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import seocho.ontology, sys;"
            "loaded=[m for m in sys.modules if m.startswith('seocho.ontology.')];"
            "assert len(loaded) <= 2, loaded",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
