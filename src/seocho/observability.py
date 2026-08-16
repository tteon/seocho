from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, ContextManager, Dict, Iterator, Optional, TypeVar


F = TypeVar("F", bound=Callable)

# Name of the pipeline stage currently executing. Every stage in the retrieval
# pipeline passes through ``StageTimer.stage``, so setting it there covers the
# traced and untraced stages alike without touching each call site.
#
# Deliberately independent of tracing: ``tracing.start_span`` returns a no-op
# span and never pushes its stack when no backend is configured, and its stack
# holds ``(trace_id, span_id)`` rather than names — so it cannot answer "which
# stage is running" even when tracing is on.
_stage_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "seocho_pipeline_stage", default=""
)


def current_stage() -> str:
    """Name of the innermost active pipeline stage, or "" outside one."""
    return _stage_var.get()


@dataclass
class StageTimer:
    """Collect named stage timings for traces and benchmark artifacts."""

    started_at: float = field(default_factory=perf_counter)
    _durations_ms: Dict[str, float] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = perf_counter()
        token = _stage_var.set(name)
        try:
            yield
        finally:
            try:
                _stage_var.reset(token)
            except ValueError:
                # Reset across a context boundary (e.g. a stage opened in one
                # task and closed in another). Timing is still recorded.
                pass
            self.record(name, (perf_counter() - started) * 1000.0)

    def record(self, name: str, elapsed_ms: float) -> None:
        key = name if name.endswith("_ms") else f"{name}_ms"
        self._durations_ms[key] = round(float(elapsed_ms), 2)

    def mark_total(self, name: str = "total") -> None:
        self.record(name, (perf_counter() - self.started_at) * 1000.0)

    def to_dict(self) -> Dict[str, float]:
        return dict(self._durations_ms)


def timed_stage(timer: StageTimer, name: str) -> Callable[[F], F]:
    """Decorator form for small observable helper functions."""

    def decorator(func: F) -> F:
        def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
            with timer.stage(name):
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


# --- LLM call observation -------------------------------------------------
#
# An out-of-tree instrument (the serve-track KV rig) needs to know the wall-clock
# extent of every LLM call and which stage issued it, because vLLM's KV-cache
# events carry no request id and cannot otherwise be attributed. Rather than
# have the SDK depend on that rig, it exposes this seam and the rig installs
# itself into it.
#
# Opt-in is explicit — installing an observer is a function call, never an env
# var or an import side effect. Nothing observes unless something asked to.

_llm_call_observer: Optional[Callable[..., ContextManager[Any]]] = None


def set_llm_call_observer(
    observer: Optional[Callable[..., ContextManager[Any]]],
) -> None:
    """Install (or clear, with ``None``) an observer around every LLM call.

    ``observer`` is called as a context-manager factory with keyword arguments
    ``role``, ``model``, ``provider``, ``prompt_chars`` and ``prompt_sections``,
    and should yield a handle the caller may stamp with provider-verbatim
    ``usage``. ``WindowRecorder.record_step`` in the serve-track rig matches
    this shape.
    """
    global _llm_call_observer
    _llm_call_observer = observer


def get_llm_call_observer() -> Optional[Callable[..., ContextManager[Any]]]:
    return _llm_call_observer


# Byte offsets at which a prompt-prefix hash is taken. Powers of two give log
# granularity for a couple of hashes per call, which is enough to locate where a
# prompt stops being byte-stable without ever storing the prompt itself.
_PREFIX_CHECKPOINTS = (256, 512, 1024, 2048, 4096, 8192)


def prefix_checkpoints(text: str) -> Dict[str, str]:
    """Short hashes of ``text`` truncated at each checkpoint it reaches.

    Content-free by construction: only digests leave the process. Two calls
    agreeing at checkpoint N shared at least N bytes of prompt, which is the
    ceiling on what prefix caching can reuse between them.
    """
    import hashlib

    out: Dict[str, str] = {}
    for size in _PREFIX_CHECKPOINTS:
        if len(text) < size:
            break
        out[str(size)] = hashlib.sha256(text[:size].encode("utf-8")).hexdigest()[:16]
    return out


@contextmanager
def observe_llm_call(
    *,
    role: str = "",
    model: str = "",
    provider: str = "",
    prompt_chars: int = 0,
    prompt_sections: Optional[Dict[str, int]] = None,
    prefix_hashes: Optional[Dict[str, str]] = None,
) -> Iterator[Any]:
    """Wrap one LLM call for the installed observer; a no-op when none is.

    Observer failures never escape — instrumentation must not break a query.
    Business exceptions do propagate, and the observer is told about them
    first so a failed call still closes its window: a call that failed still
    consumed cache, and dropping it would bias attribution.
    """
    observer = _llm_call_observer
    if observer is None:
        yield None
        return

    try:
        manager = observer(
            role=role or current_stage(),
            model=model,
            provider=provider,
            prompt_chars=prompt_chars,
            prompt_sections=dict(prompt_sections or {}),
            prefix_hashes=dict(prefix_hashes or {}),
        )
        handle = manager.__enter__()
    except Exception:
        yield None
        return

    try:
        yield handle
    except BaseException as exc:
        try:
            manager.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            pass


__all__ = [
    "StageTimer",
    "timed_stage",
    "current_stage",
    "prefix_checkpoints",
    "observe_llm_call",
    "set_llm_call_observer",
    "get_llm_call_observer",
]
