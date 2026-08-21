"""The boundary checker must actually catch violations.

A guard that passes on a real violation is worse than no guard: it converts an
unchecked boundary into a checked-looking one. The two shell contracts this
sits beside are ~90 literal grep assertions that pass while
`runtime/agent_server.py` imports eight extraction modules, which is exactly
the failure mode being pinned here.

These tests plant violations in a temporary tree and assert the checker
notices — including the one case grep gets wrong, an import inside a docstring.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_import_boundaries", _ROOT / "scripts" / "ci" / "check-import-boundaries.py"
)
checker = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = checker
_spec.loader.exec_module(checker)


def _imports(tmp_path: Path, source: str):
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    # iter_imports reports paths relative to ROOT; point it at the temp tree.
    original = checker.ROOT
    checker.ROOT = tmp_path
    try:
        return list(checker.iter_imports(path))
    finally:
        checker.ROOT = original


def test_a_module_level_import_is_found(tmp_path):
    found = _imports(tmp_path, "import extraction.debate\n")
    assert [(i.module, i.is_deferred) for i in found] == [("extraction.debate", False)]


def test_a_deferred_import_is_found_and_flagged(tmp_path):
    """The one real reverse edge in this repo hides inside a function."""
    found = _imports(tmp_path, "def f():\n    from runtime.x import y\n    return y\n")
    assert [(i.module, i.is_deferred) for i in found] == [("runtime.x", True)]


def test_an_import_inside_a_docstring_is_not_an_import(tmp_path):
    """grep cannot tell these apart; this is why the checker parses.

    `src/seocho/agent/runtime_factory.py` carries exactly this shape and was
    reported as an SDK-to-runtime violation by a text search. It is not one.
    """
    source = '"""Usage::\n\n    from runtime.ontology_registry import get_it\n"""\n'
    assert _imports(tmp_path, source) == []


def test_a_relative_import_never_crosses_a_boundary(tmp_path):
    assert _imports(tmp_path, "from ..store.graph import GraphStore\n") == []


def test_ownership_is_decided_by_path():
    assert checker.owner_of("src/seocho/query/intent.py") == "seocho"
    assert checker.owner_of("runtime/agent_server.py") == "runtime"
    assert checker.owner_of("extraction/config.py") == "extraction"
    assert checker.owner_of("scripts/demo/x.py") == "other"


def test_tests_are_excluded_from_production_scan():
    """A test importing across a boundary is a fixture, not an architecture break."""
    paths = {str(p) for p in checker.production_files()}
    assert not any("/tests/" in p or "/test_" in p for p in paths)


def test_the_repository_currently_satisfies_the_contracts(monkeypatch):
    """Fails loudly if someone lands a new violation or grows the ratchet."""
    monkeypatch.setattr(sys, "argv", ["check-import-boundaries.py"])
    assert checker.main() == 0
