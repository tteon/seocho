"""Compatibility alias for :mod:`runtime.runtime_ingest`."""

from importlib import import_module as _import_module
import sys as _sys


_sys.modules[__name__] = _import_module("runtime.runtime_ingest")
