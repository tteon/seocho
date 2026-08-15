"""Tracing seams for the legacy extraction/runtime services.

This module used to be an Opik integration and nothing else: it configured the
Opik client, wrapped OpenAI clients with Opik's tracker, and forwarded span and
trace attributes to Opik's context. Opik was removed (`ADR-0172`) in favour of
the SDK's own metric and tracing surface, so nothing here exports to a vendor
any more.

The functions are kept, and kept no-op, on purpose. Eight modules across
`extraction/` and `runtime/` import `track`, `wrap_openai_client`,
`update_current_span` and `update_current_trace`, and `@track` is applied as a
decorator at import time. Deleting the module would break those call sites for
no gain, and `extraction/` is a compatibility surface under `CLAUDE.md` — it
owns legacy behaviour, not new instrumentation. Anything that should be
measured belongs in `src/seocho/metrics.py`, which already carries the four
golden signals, or in `src/seocho/tracing.py`.

So: these are seams that do nothing, deliberately, rather than seams that
quietly ship data to a third party.
"""

import inspect
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def wrap_openai_client(client: Any) -> Any:
    """Return the client untouched.

    Previously wrapped it in Opik's `track_openai`. Returning the client as-is
    keeps every caller's type and behaviour identical.
    """
    return client


def track(name: str) -> Callable[[F], F]:
    """Return a decorator that leaves the function exactly as it is.

    Async functions must stay async and sync stay sync, since callers await the
    results of decorated coroutines — hence the `inspect` check rather than a
    blanket passthrough.
    """

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):
            return func
        return func

    return decorator


def update_current_span(**kwargs: Any) -> None:
    """Retained no-op; there is no current vendor span to update."""
    return None


def update_current_trace(**kwargs: Any) -> None:
    """Retained no-op; there is no current vendor trace to update."""
    return None
