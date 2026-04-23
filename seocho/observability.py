from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Callable, Dict, Iterator, TypeVar


F = TypeVar("F", bound=Callable)


@dataclass
class StageTimer:
    """Collect named stage timings for traces and benchmark artifacts."""

    started_at: float = field(default_factory=perf_counter)
    _durations_ms: Dict[str, float] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        finally:
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


__all__ = ["StageTimer", "timed_stage"]
