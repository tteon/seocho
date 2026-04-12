from __future__ import annotations

from pathlib import Path


def evaluation_output_dir(name: str) -> Path:
    """Return an ignored output directory for example/evaluation artifacts."""
    path = Path(__file__).resolve().parent.parent / "outputs" / "evaluation" / name
    path.mkdir(parents=True, exist_ok=True)
    return path
