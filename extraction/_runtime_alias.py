"""Compatibility helpers for legacy flat ``extraction/*`` imports."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType


def alias_runtime_module(alias_name: str, runtime_module: str) -> ModuleType:
    """Resolve a canonical ``runtime.*`` module from legacy extraction paths."""

    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    module = import_module(runtime_module)
    sys.modules[alias_name] = module
    return module
