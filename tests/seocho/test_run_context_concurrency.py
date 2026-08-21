"""Run-context concurrency isolation (structured runtime B7 fix).

The in-flight per-request run context must live in a ContextVar isolated per
execution context, never on a shared instance attribute — otherwise concurrent
multi-tenant asks() clobber each other and tenant A's workspace/pin attaches to
tenant B's answer.
"""

from __future__ import annotations

import threading

from seocho.local_engine import _ACTIVE_RUN_CONTEXT, active_run_context
from seocho.ontology.run_context import OntologyRunContext


def test_no_shared_instance_attr_for_inflight_context():
    from seocho.local_engine import _LocalEngine
    # The clobber-prone shared attribute is gone; the accessor is post-hoc only.
    assert not hasattr(_LocalEngine, "_current_run_context")
    assert hasattr(_LocalEngine, "last_run_context")


def test_active_run_context_is_isolated_across_threads():
    """Two threads each set their own request context; neither sees the other's."""
    results: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def worker(ws: str) -> None:
        token = _ACTIVE_RUN_CONTEXT.set(OntologyRunContext(workspace_id=ws))
        try:
            barrier.wait(timeout=5)          # both set before either reads
            seen = active_run_context()
            results[ws] = seen.workspace_id if seen else "<none>"
        finally:
            _ACTIVE_RUN_CONTEXT.reset(token)

    ta = threading.Thread(target=worker, args=("acme",))
    tg = threading.Thread(target=worker, args=("globex",))
    ta.start(); tg.start(); ta.join(5); tg.join(5)

    assert results == {"acme": "acme", "globex": "globex"}, (
        "each thread must see ONLY its own tenant's run context"
    )
    # outside any request, there is no active context
    assert active_run_context() is None
