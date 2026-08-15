"""Per-chapter JSONL tracing setup for the teaching notebooks.

Was `opik_setup.py`. `ADR-0166` removed the Opik backend, so a chapter's traces
now land in a local JSONL file and nowhere else — no workspace, no project, no
API key, nothing leaving the machine. That is a better default for teaching
material anyway: a learner can open `./traces/chapter_03.jsonl` and read what
the SDK did, without signing up for anything.

Contract:
    setup_tracing("03") enables the jsonl backend at ./traces/chapter_03.jsonl
    and returns the path it wrote to.

Override knobs:
    TEACHING_TRACE_DIR     directory for the JSONL files (default: ./traces)
    SEOCHO_TRACE_BACKEND   honoured as-is if already set; this helper does not
                           override an explicit choice.

The old names (`setup_opik`, `teardown_opik`, `opik_console_link`) are kept as
thin aliases because six chapter notebooks call them, and rewriting executed
notebook JSON to chase a rename is churn with no benefit to a reader. They are
scheduled for removal once the notebooks are next regenerated.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

DEFAULT_TRACE_DIR = "./traces"


def _trace_path(chapter: str) -> Path:
    directory = Path(os.environ.get("TEACHING_TRACE_DIR", DEFAULT_TRACE_DIR))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"chapter_{str(chapter).zfill(2)}.jsonl"


def setup_tracing(chapter: str, *, verbose: bool = True) -> Path:
    """Enable the JSONL trace backend for one chapter and return its path."""
    from seocho.tracing import enable_tracing

    path = _trace_path(chapter)
    enable_tracing(backend="jsonl", output=str(path))
    if verbose:
        print(f"tracing -> {path}")
    return path


def teardown_tracing() -> None:
    """Flush and disable tracing. Safe to call when tracing was never enabled."""
    from seocho.tracing import disable_tracing

    disable_tracing()


def trace_file(chapter: str) -> Optional[Path]:
    """Return the chapter's trace file if it exists yet, else None."""
    path = _trace_path(chapter)
    return path if path.exists() else None


# Back-compat aliases for the six chapter notebooks. Remove when they are
# regenerated; see the module docstring.
setup_opik = setup_tracing
teardown_opik = teardown_tracing
opik_console_link = trace_file

__all__ = [
    "setup_tracing",
    "teardown_tracing",
    "trace_file",
    "setup_opik",
    "teardown_opik",
    "opik_console_link",
]
