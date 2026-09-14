"""Local JSONL tracing for teaching notebooks; no third-party account needed."""
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


__all__ = ["setup_tracing", "teardown_tracing", "trace_file"]
