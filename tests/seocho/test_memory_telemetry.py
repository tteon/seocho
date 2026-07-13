import pytest

from seocho.memory import MemoryCommitMetricsObserver
from seocho.metrics import ProductionMetrics


class Instrument:
    def __init__(self) -> None:
        self.calls = []

    def record(self, value, attributes=None):
        self.calls.append((value, attributes))

    def add(self, value, attributes=None):
        self.calls.append((value, attributes))

    set = add


class Meter:
    def __init__(self) -> None:
        self.instruments = {}

    def _make(self, name, **_):
        self.instruments[name] = Instrument()
        return self.instruments[name]

    create_counter = _make
    create_up_down_counter = _make
    create_histogram = _make
    create_gauge = _make


def test_phase_observer_exports_only_phase_and_outcome() -> None:
    meter = Meter()
    observer = MemoryCommitMetricsObserver(ProductionMetrics(meter))

    observer.record("sequence_allocate", 12.5, "ok")

    assert meter.instruments["seocho.memory.commit.phase.duration"].calls == [
        (12.5, {"phase": "sequence_allocate", "outcome": "ok"})
    ]


def test_phase_observer_rejects_unbounded_phase_values() -> None:
    observer = MemoryCommitMetricsObserver(ProductionMetrics())
    with pytest.raises(ValueError, match="unsupported memory commit phase"):
        observer.record("wallet-123", 1.0, "ok")
