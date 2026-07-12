from seocho.eval.evaluation_telemetry import emit_query_evaluation, emit_scenario_status
from seocho.metrics import ProductionMetrics


class Instrument:
    def __init__(self): self.calls = []
    def add(self, value, attributes=None): self.calls.append((value, attributes))
    record = add
    set = add


class Meter:
    def __init__(self): self.instruments = {}
    def create_counter(self, name, **kwargs): return self._create(name)
    create_histogram = create_counter
    create_gauge = create_counter
    create_up_down_counter = create_counter
    def _create(self, name): self.instruments[name] = Instrument(); return self.instruments[name]


def test_evaluation_metrics_are_bounded_and_aggregate() -> None:
    meter = Meter()
    metrics = ProductionMetrics(meter)
    emit_scenario_status("S8", status="passed", metrics=metrics)
    emit_query_evaluation(cohort="customer-template-10k", total=100, correct=99, metrics=metrics)
    assert meter.instruments["seocho.evaluation.scenario.status"].calls == [(1, {"scenario_id": "S8", "status": "passed"})]
    assert meter.instruments["seocho.evaluation.query.accuracy"].calls == [(0.99, {"cohort": "customer-template-10k"})]
