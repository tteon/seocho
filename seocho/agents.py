"""Backward-compatible agent factory surface.

Canonical agent/session factories now live under :mod:`seocho.agent`.
This module remains as a compatibility shim for existing imports.
"""

from .agent.factory import (
    create_indexing_agent,
    create_query_agent,
    create_supervisor_agent,
    indexing_system_prompt as _indexing_system_prompt,
    query_system_prompt as _query_system_prompt,
    supervisor_system_prompt as _supervisor_system_prompt,
)

__all__ = [
    "create_indexing_agent",
    "create_query_agent",
    "create_supervisor_agent",
    "_indexing_system_prompt",
    "_query_system_prompt",
    "_supervisor_system_prompt",
]
