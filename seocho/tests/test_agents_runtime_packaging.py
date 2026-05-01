"""Packaging-level regression tests for the agents_runtime relocation.

Background: ``seocho.session`` used to import ``from extraction.agents_runtime
import get_agents_runtime``. ``extraction/`` is a sibling of ``seocho/`` in
the GitHub repo but is *not* shipped with the published wheel
(``pyproject.toml``: ``include = ["seocho*"]``), so any ``pip install seocho``
consumer that touched agent / supervisor mode crashed with
``ModuleNotFoundError: No module named 'extraction'``.

The fix moved the canonical adapter into ``seocho.agents_runtime`` and left
``extraction/agents_runtime.py`` as a re-export shim. These tests pin that
shape so the regression cannot return.
"""

from __future__ import annotations


def test_canonical_module_is_under_seocho() -> None:
    """``seocho.agents_runtime`` exposes the adapter and factory."""
    from seocho.agents_runtime import AgentsRuntimeAdapter, get_agents_runtime

    runtime = get_agents_runtime()
    assert isinstance(runtime, AgentsRuntimeAdapter)


def test_extraction_shim_reexports_same_objects() -> None:
    """The legacy import path stays valid and refers to the same classes.

    Server-side code (``extraction/debate.py``, server tests) and any
    out-of-tree consumer that imported ``extraction.agents_runtime`` directly
    must keep working without modification.
    """
    from seocho import agents_runtime as canonical
    from extraction import agents_runtime as shim

    assert shim.AgentsRuntimeAdapter is canonical.AgentsRuntimeAdapter
    assert shim.get_agents_runtime is canonical.get_agents_runtime


def test_session_does_not_import_from_extraction() -> None:
    """``seocho.session`` must resolve the adapter via the seocho package.

    A regression here means a future edit re-introduced
    ``from extraction.agents_runtime import …`` inside ``session.py`` and
    the wheel-only install path is broken again.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "session.py"
    text = src.read_text()
    assert "from extraction.agents_runtime" not in text, (
        "seocho.session must import the adapter via seocho.agents_runtime "
        "(see test docstring for context)."
    )
    assert "from .agents_runtime import" in text or "from seocho.agents_runtime import" in text, (
        "seocho.session lost its canonical import of the agents_runtime adapter."
    )
