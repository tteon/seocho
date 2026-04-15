"""Canonical runtime deployment shell package.

This package is the long-term replacement for the historically overloaded
``extraction/`` shell. During the staged migration, most runtime modules still
depend on flat modules that live under ``extraction/``. Bootstrap that path
here so ``runtime.*`` modules can import those remaining helpers without
requiring every downstream caller to mutate ``sys.path`` first.
"""

from __future__ import annotations

import sys
from pathlib import Path


_EXTRACTION_DIR = Path(__file__).resolve().parent.parent / "extraction"
_EXTRACTION_DIR_STR = str(_EXTRACTION_DIR)

if _EXTRACTION_DIR_STR not in sys.path:
    sys.path.insert(0, _EXTRACTION_DIR_STR)
