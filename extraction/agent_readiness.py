"""Compatibility alias for :mod:`runtime.agent_readiness`."""

try:
    from ._runtime_alias import alias_runtime_module as _alias_runtime_module
except ImportError:
    from _runtime_alias import alias_runtime_module as _alias_runtime_module


_alias_runtime_module(__name__, "runtime.agent_readiness")
