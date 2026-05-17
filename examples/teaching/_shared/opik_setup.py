"""Per-chapter Opik + JSONL tracing setup for teaching notebooks.

Contract:
    setup_opik("01") enables Opik (workspace from $OPIK_WORKSPACE, default 'seocho')
    plus JSONL backend writing to ./traces/chapter_01.jsonl.
    The Opik project name defaults to ``teaching-ch{N}-{user}`` so multiple
    invited members of the same workspace can each have their own project
    by setting ``OPIK_USER`` in ``.env`` (or via Colab Secrets).

Override knobs:
    OPIK_WORKSPACE              workspace name (default: seocho)
    OPIK_USER                   member identifier injected into project name
    TEACHING_OPIK_PROJECT       full override; takes precedence over the default
    SEOCHO_TRACE_BACKEND        set to ``none`` to disable Opik+JSONL entirely

References:
    https://www.comet.com/docs/opik/evaluation/evaluate_agents

Compatibility note:
    Newer seocho exports ``current_backend_names``, ``is_observability_degraded``
    and ``tracing_degraded_reasons``. Older PyPI releases of seocho only ship
    ``enable_tracing`` / ``disable_tracing``. This module imports the optional
    helpers with try/except so it runs on either.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from seocho.tracing import disable_tracing, enable_tracing  # always present


# -- Optional helpers (added in newer seocho releases) -----------------------

try:  # type: ignore[no-redef]
    from seocho.tracing import current_backend_names as _current_backend_names
except ImportError:  # pragma: no cover — old PyPI
    def _current_backend_names() -> List[str]:
        return []


try:  # type: ignore[no-redef]
    from seocho.tracing import is_observability_degraded as _is_observability_degraded
except ImportError:  # pragma: no cover — old PyPI
    def _is_observability_degraded() -> bool:
        return False


try:  # type: ignore[no-redef]
    from seocho.tracing import tracing_degraded_reasons as _tracing_degraded_reasons
except ImportError:  # pragma: no cover — old PyPI
    def _tracing_degraded_reasons() -> List[str]:
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _resolve_project(chapter: str) -> str:
    explicit = os.getenv("TEACHING_OPIK_PROJECT")
    if explicit:
        return explicit
    user = os.getenv("OPIK_USER", "anonymous")
    return f"teaching-ch{chapter}-{user}"


def setup_opik(
    chapter: str,
    *,
    jsonl_dir: str = "./traces",
    workspace: Optional[str] = None,
    only_jsonl: bool = False,
) -> dict:
    """Enable tracing for a chapter notebook.

    Parameters
    ----------
    chapter:
        Chapter identifier embedded in the JSONL filename and Opik project
        (e.g. ``"01"``).
    jsonl_dir:
        Directory for the chapter JSONL trace file.
    workspace:
        Override the Opik workspace; defaults to $OPIK_WORKSPACE then 'seocho'.
    only_jsonl:
        If True, skip the Opik backend (useful when running offline or before
        an API key is configured).

    Returns
    -------
    dict with keys ``project``, ``workspace``, ``jsonl_path``, ``backends``,
    ``degraded``, ``degraded_reasons``.
    """
    if os.getenv("SEOCHO_TRACE_BACKEND", "").strip().lower() == "none":
        return {
            "project": None,
            "workspace": None,
            "jsonl_path": None,
            "backends": [],
            "degraded": False,
            "degraded_reasons": [],
        }

    project = _resolve_project(chapter)
    workspace = workspace or os.getenv("OPIK_WORKSPACE") or "seocho"
    jsonl_path = str(Path(jsonl_dir) / f"chapter_{chapter}.jsonl")
    Path(jsonl_dir).mkdir(parents=True, exist_ok=True)

    backends = ["jsonl"] if only_jsonl else ["opik", "jsonl"]

    # Older seocho versions may not support the ``workspace`` kwarg on
    # enable_tracing. Try the full signature first, then degrade.
    try:
        enable_tracing(
            backend=backends,
            output=jsonl_path,
            workspace=workspace,
            project_name=project,
        )
    except TypeError:
        os.environ["OPIK_WORKSPACE"] = workspace  # honoured by OpikBackend internally
        enable_tracing(
            backend=backends,
            output=jsonl_path,
            project_name=project,
        )

    return {
        "project": project,
        "workspace": workspace,
        "jsonl_path": jsonl_path,
        "backends": _current_backend_names() or backends,
        "degraded": _is_observability_degraded(),
        "degraded_reasons": _tracing_degraded_reasons(),
    }


def teardown_opik() -> None:
    """Disable tracing — call at the end of a notebook to flush Opik."""
    disable_tracing()


def opik_console_link(project: Optional[str]) -> Optional[str]:
    """Convenience: return the Opik UI URL for the chapter's project, if known."""
    if not project:
        return None
    workspace = os.getenv("OPIK_WORKSPACE", "seocho")
    return f"https://www.comet.com/opik/{workspace}/projects/{project}"


__all__ = ["setup_opik", "teardown_opik", "opik_console_link"]
