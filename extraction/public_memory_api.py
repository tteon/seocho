"""Compatibility alias for :mod:`runtime.public_memory_api`."""

from importlib import import_module as _import_module
import sys as _sys


_sys.modules[__name__] = _import_module("runtime.public_memory_api")
