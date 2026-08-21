"""The grouped views must be additive, and the two facades must not drift.

`Seocho` had 80 public methods with 23 in no category at all, and `AsyncSeocho`
had 56 — 25 sync methods with no async counterpart and no declared reason
(`seocho-6yf`). These tests pin both halves of the fix: the namespaces add
without removing, and the async surface is generated so it cannot fall behind
again.
"""

from __future__ import annotations

import inspect

import pytest

from seocho.client import AsyncSeocho, Seocho
from seocho.client_namespaces import (
    IndexNamespace,
    OntologyNamespace,
    PlatformNamespace,
    SessionNamespace,
)

_NAMESPACES = {
    "index": IndexNamespace,
    "governance": OntologyNamespace,
    "platform": PlatformNamespace,
    "sessions": SessionNamespace,
}

# Constructors return a Seocho, so an async factory would misdescribe the
# object; close() must not offload teardown to a worker thread; properties
# read state rather than act.
_INTENTIONALLY_SYNC_ONLY = {
    "local", "remote", "from_agent_design", "from_indexing_design",
    "from_runtime_bundle", "close", "last_query_metadata",
}


@pytest.mark.parametrize("attr,cls", sorted(_NAMESPACES.items()))
def test_namespace_is_exposed_on_both_facades(attr, cls):
    for facade in (Seocho, AsyncSeocho):
        prop = getattr(facade, attr, None)
        assert isinstance(prop, property), f"{facade.__name__}.{attr} is not a property"


@pytest.mark.parametrize("cls", sorted(_NAMESPACES.values(), key=lambda c: c.__name__))
def test_every_mapping_points_at_a_real_method(cls):
    """A typo here would be a silently broken namespace member."""
    missing = [t for t in cls._METHODS.values() if not hasattr(Seocho, t)]
    assert not missing, f"{cls.__name__} maps to nonexistent methods: {missing}"


def test_namespaces_are_additive_not_replacements():
    """Every flat method a namespace covers must still be callable directly."""
    for cls in _NAMESPACES.values():
        for target in cls._METHODS.values():
            assert hasattr(Seocho, target), f"{target} disappeared"


def test_namespace_resolves_through_the_owner_each_time():
    """No caching: a patched method must be visible through the namespace.

    A namespace that captured bound methods at construction would keep serving
    the pre-patch callable — the same failure that made the ontology package
    use sys.modules aliases rather than forwarding shims.
    """
    client = object.__new__(Seocho)
    sentinel = object()
    client.index_file = lambda *a, **k: sentinel  # type: ignore[method-assign]
    assert IndexNamespace(client).file() is sentinel


def test_namespace_rejects_unknown_members_with_a_useful_error():
    ns = IndexNamespace(object.__new__(Seocho))
    with pytest.raises(AttributeError) as excinfo:
        ns.no_such_operation
    assert "available:" in str(excinfo.value)


def test_namespace_is_read_only():
    ns = IndexNamespace(object.__new__(Seocho))
    with pytest.raises(AttributeError):
        ns.file = lambda: None


def test_async_surface_covers_the_sync_one():
    """The regression this whole change exists to prevent."""
    sync = {n for n in dir(Seocho) if not n.startswith("_")}
    asyn = {n for n in dir(AsyncSeocho) if not n.startswith("_")}
    gap = sync - asyn - _INTENTIONALLY_SYNC_ONLY
    assert not gap, f"AsyncSeocho is missing: {sorted(gap)}"


def test_generated_delegates_are_coroutines():
    for name in ("index_file", "index_directory", "reindex", "execute_query"):
        assert inspect.iscoroutinefunction(getattr(AsyncSeocho, name)), name


def test_hand_written_async_methods_are_not_overwritten():
    """The generator fills gaps only; an explicit coroutine must keep winning."""
    assert "add" in vars(AsyncSeocho)
    assert inspect.iscoroutinefunction(AsyncSeocho.add)


def test_query_and_agents_are_still_plain_methods():
    """Documented exclusion: namespacing them would shadow a public callable."""
    for name in ("query", "agents"):
        assert not isinstance(getattr(Seocho, name), property), name
