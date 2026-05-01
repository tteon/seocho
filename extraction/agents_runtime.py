"""
Re-export shim — canonical implementation lives in ``seocho.agents_runtime``.

The adapter was relocated into the ``seocho`` package so that ``pip install
seocho`` consumers (and Jupyter / examples / SDK callers running the bare
seocho install) get agent-mode support without needing the repo's
``extraction/`` directory on ``sys.path``.

This shim preserves the legacy import paths used by server-side code and
older tests:

- ``from extraction.agents_runtime import get_agents_runtime``
- ``import agents_runtime`` (when ``extraction/`` is on ``pythonpath`` via
  pytest config — see ``pyproject.toml`` ``[tool.pytest.ini_options]``)

New code should import from :mod:`seocho.agents_runtime` directly.
"""

from seocho.agents_runtime import (  # noqa: F401
    AgentsRuntimeAdapter,
    get_agents_runtime,
)

__all__ = ["AgentsRuntimeAdapter", "get_agents_runtime"]
