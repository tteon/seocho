from seocho.query.otel_observability import OTelBridge


def test_noop_bridge_is_safe_without_otel_dependencies() -> None:
    bridge = OTelBridge()
    with bridge.span("sdcr.route", attributes={"workspace_id": "w1"}) as span:
        span.set_attribute("selected_views", 2)
    bridge.record_route("slot_gap", 2)
    bridge.record_agent_call("financials")
    bridge.record_usage(model="m", total_tokens=4, cost_usd=0.01, latency_ms=2.0)


def test_bridge_updates_fake_meter_instruments() -> None:
    class Instrument:
        def __init__(self):
            self.values = []

        def add(self, value, attributes=None):
            self.values.append((value, attributes))

        def record(self, value, attributes=None):
            self.values.append((value, attributes))

    class Meter:
        def __init__(self):
            self.instruments = []

        def create_counter(self, _name):
            item = Instrument()
            self.instruments.append(item)
            return item

        def create_histogram(self, _name):
            item = Instrument()
            self.instruments.append(item)
            return item

    meter = Meter()
    bridge = OTelBridge(meter=meter)
    bridge.record_usage(model="m", total_tokens=3, cost_usd=None, latency_ms=1)
    assert any(values for item in meter.instruments for values in item.values)
