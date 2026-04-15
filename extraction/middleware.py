"""Compatibility alias for :mod:`runtime.middleware`."""

try:
    from ._runtime_alias import alias_runtime_module as _alias_runtime_module
except ImportError:
    from _runtime_alias import alias_runtime_module as _alias_runtime_module


_alias_runtime_module(__name__, "runtime.middleware")
