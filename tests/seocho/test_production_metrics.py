import pytest

from seocho.metrics import METRIC_SPECS, ProductionMetrics


class Instrument:
    def __init__(self) -> None:
        self.calls = []

    def add(self, value, attributes=None):
        self.calls.append(("add", value, attributes))

    def record(self, value, attributes=None):
        self.calls.append(("record", value, attributes))

    def set(self, value, attributes=None):
        self.calls.append(("set", value, attributes))


class Meter:
    def __init__(self) -> None:
        self.instruments = {}

    def _make(self, name, **kwargs):
        self.instruments[name] = Instrument()
        return self.instruments[name]

    create_counter = _make
    create_up_down_counter = _make
    create_histogram = _make
    create_gauge = _make


def test_catalog_covers_every_production_plane() -> None:
    prefixes = {name.split(".")[1] for name in METRIC_SPECS if name.startswith("seocho.")}
    assert {"agent", "answer", "memory", "projection", "retrieval", "context", "gen_ai", "governance"} <= prefixes
    assert "seocho.retrieval.inflight" in METRIC_SPECS
    assert "seocho.retrieval.admission_rejection.count" in METRIC_SPECS


def test_records_only_declared_bounded_attributes() -> None:
    meter = Meter()
    metrics = ProductionMetrics(meter)
    metrics.record(
        "seocho.agent.request.duration",
        0.25,
        {"operation": "memory_answer", "outcome": "success"},
    )
    assert meter.instruments["seocho.agent.request.duration"].calls == [
        ("record", 0.25, {"operation": "memory_answer", "outcome": "success"})
    ]
    with pytest.raises(ValueError, match="unsupported attributes"):
        metrics.record(
            "seocho.agent.request.duration", 0.1, {"wallet_id": "secret"}
        )


def test_rejects_wrong_instrument_operation_and_unbounded_values() -> None:
    metrics = ProductionMetrics(Meter())
    with pytest.raises(TypeError, match="not a histogram"):
        metrics.record("seocho.agent.request.count", 1)
    with pytest.raises(ValueError, match="exceeds 80"):
        metrics.add(
            "seocho.memory.commit.count",
            attributes={"outcome": "x" * 81},
        )


def test_noop_registry_still_enforces_privacy_contract() -> None:
    metrics = ProductionMetrics()
    metrics.set("seocho.memory.sequence", 10)
    with pytest.raises(ValueError, match="unsupported attributes"):
        metrics.set("seocho.memory.sequence", 10, {"workspace_id": "tenant"})
