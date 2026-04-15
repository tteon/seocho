"""Canonical runtime deployment shell package.

This package is the long-term replacement for the historically overloaded
``extraction/`` shell. During the staged migration, most runtime modules still
depend on flat modules that live under ``extraction/``. Bootstrap the known
legacy helper locations here so ``runtime.*`` modules can import those
remaining helpers without requiring every downstream caller to mutate
``sys.path`` first.
"""

from __future__ import annotations

import sys
from pathlib import Path


_ROOT_DIR = Path(__file__).resolve().parent.parent

for _helper_dir in (_ROOT_DIR, _ROOT_DIR / "extraction"):
    if not _helper_dir.exists():
        continue
    _helper_dir_str = str(_helper_dir)
    if _helper_dir_str not in sys.path:
        sys.path.insert(0, _helper_dir_str)
