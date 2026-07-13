from seocho.eval.longitudinal_memory import generate_longitudinal_events
from seocho.eval.memory_framework_benchmark import (
    CapabilityStatus,
    MemoryCapabilities,
    MemoryObservation,
    build_temporal_cases,
    qualify_adapter,
)


class FakeAdapter:
    framework = "fake"
    capabilities = MemoryCapabilities(*([CapabilityStatus.NATIVE] * 7))

    def __init__(self) -> None:
        self.histories = {}
        self.keys = set()

    def reset(self) -> None:
        self.histories.clear()
        self.keys.clear()

    def add(self, event):
        if event.idempotency_key in self.keys:
            return False
        self.keys.add(event.idempotency_key)
        self.histories.setdefault(event.transaction_ref, []).append(event)
        return True

    @staticmethod
    def _observation(event):
        return MemoryObservation(
            memory_id=event.transaction_ref,
            state=event.state,
            sequence=event.sequence,
            provenance_refs=(event.provenance_id,),
        )

    def get_current(self, memory_id):
        history = self.histories.get(memory_id, [])
        return self._observation(history[-1]) if history else None

    def get_at_sequence(self, memory_id, sequence):
        history = [
            event
            for event in self.histories.get(memory_id, [])
            if event.sequence <= sequence
        ]
        return self._observation(history[-1]) if history else None

    def search(self, query, *, limit):
        return ()


def test_common_temporal_qualification_scores_current_historical_and_absence() -> None:
    events = tuple(generate_longitudinal_events(event_count=30, seed=7))
    cases = build_temporal_cases(events, sample_memories=5)

    report = qualify_adapter(FakeAdapter(), events, cases)

    assert report["events"] == 30
    assert report["ingestion"]["applied"] == 30
    assert report["idempotent_replay"]["applied_twice"] is False
    assert report["retrieval"]["accuracy"] == 1.0
    assert {row["operation"] for row in report["rows"]} == {
        "current",
        "point_in_time",
    }


def test_unsupported_temporal_capability_is_not_scored_as_failure() -> None:
    adapter = FakeAdapter()
    adapter.capabilities = MemoryCapabilities(
        current_read=CapabilityStatus.NATIVE,
        point_in_time_read=CapabilityStatus.UNSUPPORTED,
        temporal_invalidation=CapabilityStatus.UNSUPPORTED,
        graph_relations=CapabilityStatus.UNSUPPORTED,
        idempotent_write=CapabilityStatus.NATIVE,
        rollback_or_rebuild=CapabilityStatus.UNSUPPORTED,
        provenance=CapabilityStatus.NATIVE,
    )
    events = tuple(generate_longitudinal_events(event_count=6, seed=9))
    report = qualify_adapter(adapter, events, build_temporal_cases(events))

    temporal = [row for row in report["rows"] if row["operation"] == "point_in_time"]
    assert temporal
    assert all(row["correct"] is None for row in temporal)
    assert report["retrieval"]["accuracy"] == 1.0
