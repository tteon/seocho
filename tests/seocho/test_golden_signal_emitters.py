"""The counters the alert rules depend on must actually fire.

Each test drives the production decision point and asserts the metric the
corresponding prometheus rule watches. These five were previously write-only
specs — alerts existed, emitters did not.
"""

from __future__ import annotations

import pytest

import seocho.metrics as metrics_module
from seocho.metrics import ProductionMetrics


class _Instrument:
    def __init__(self):
        self.calls = []

    def add(self, value, attributes=None):
        self.calls.append(("add", value, attributes))

    def record(self, value, attributes=None):
        self.calls.append(("record", value, attributes))

    def set(self, value, attributes=None):
        self.calls.append(("set", value, attributes))


class _Meter:
    def __init__(self):
        self.instruments = {}

    def _make(self, name, **kwargs):
        self.instruments[name] = _Instrument()
        return self.instruments[name]

    create_counter = _make
    create_up_down_counter = _make
    create_gauge = _make
    create_histogram = _make


@pytest.fixture
def meter(monkeypatch):
    meter = _Meter()
    monkeypatch.setattr(metrics_module, "_metrics", ProductionMetrics(meter))
    return meter


def test_projection_reports_backlog_after_a_batch(meter, monkeypatch):
    from seocho.memory import agent_projection
    from seocho.memory.agent_projection import (
        AgentProjectionEntry,
        AgentTransactionProjector,
    )

    # Graph-shape construction is covered by the projection tests; this one
    # holds only the backlog-reporting contract, so the payload plumbing is
    # stubbed out.
    monkeypatch.setattr(
        AgentTransactionProjector, "_build_graph", staticmethod(lambda entries: ([], []))
    )
    monkeypatch.setattr(
        agent_projection, "validate_projection_format", lambda nodes, rels: None
    )

    class Repo:
        def read_outbox_batch(self, *, workspace_id, limit):
            return (
                AgentProjectionEntry(
                    workspace_id=workspace_id,
                    sequence=7,
                    ordinal=0,
                    aggregate_id="m-1",
                    payload={},
                ),
            )

        def acknowledge_projection(self, **kwargs):
            pass

        def outbox_backlog(self, *, workspace_id):
            return 3, 42.5

    class Graph:
        def write(self, nodes, relationships, **kwargs):
            return {"nodes_created": len(nodes)}

    projector = AgentTransactionProjector(graph_store=Graph(), repository=Repo())
    projector.project_pending(workspace_id="ws", database="db")

    pending = meter.instruments["seocho.projection.outbox.pending"].calls
    oldest = meter.instruments["seocho.projection.outbox.oldest_age"].calls
    assert pending == [("set", 3, {"projection": "db"})]
    assert oldest == [("set", 42.5, {"projection": "db"})]


def test_projection_empty_batch_still_reports_zero(meter):
    from seocho.memory.agent_projection import AgentTransactionProjector

    class Repo:
        def read_outbox_batch(self, *, workspace_id, limit):
            return ()

    projector = AgentTransactionProjector(graph_store=object(), repository=Repo())
    projector.project_pending(workspace_id="ws", database="db")
    pending = meter.instruments["seocho.projection.outbox.pending"].calls
    assert pending == [("set", 0, {"projection": "db"})]


def test_json_salvage_counts_a_structured_output_repair(meter):
    from seocho.store.llm import LLMResponse

    response = LLMResponse(
        text='the answer follows {"a": 1} trailing prose', model="m2.7"
    )
    assert response.json() == {"a": 1}
    calls = meter.instruments["seocho.gen_ai.structured_output_repair.count"].calls
    assert calls == [
        (
            "add",
            1,
            {"gen_ai.request.model": "m2.7", "reason": "non_json_text_salvage"},
        )
    ]


def test_clean_json_is_not_a_repair(meter):
    from seocho.store.llm import LLMResponse

    LLMResponse(text='{"a": 1}', model="m").json()
    calls = meter.instruments["seocho.gen_ai.structured_output_repair.count"].calls
    assert calls == []


def test_stale_binding_counts_a_blocked_disclosure_violation(meter):
    from seocho.risk.preflight import (
        SubjectDisclosureBinding,
        default_disclosure_policy,
    )

    policy = default_disclosure_policy()
    binding = SubjectDisclosureBinding(
        subject_ref_hash="hash",
        role="viewer",
        policy_id=policy.policy_id,
        policy_version="stale-version",
        denied_fields=(),
    )
    with pytest.raises(ValueError):
        policy.filter_for_subject({"f": 1}, binding=binding)
    calls = meter.instruments["seocho.governance.disclosure_violation.count"].calls
    assert calls == [
        ("add", 1, {"stage": "disclosure", "policy.disposition": "blocked"})
    ]


def test_freshness_exhaustion_counts_a_violation(meter):
    from seocho.memory.postgres_resilience import PostgresReadRouter

    router = PostgresReadRouter(metrics=metrics_module.get_metrics())
    with pytest.raises(LookupError):
        router.choose([], client_region="kr")
    calls = meter.instruments["seocho.answer.freshness_violation.count"].calls
    assert calls == [("add", 1, {"query.class": "memory_read"})]


def test_admission_permits_gauge_moves_before_shedding(meter):
    from seocho.query.query_proxy import QueryAdmissionController

    controller = QueryAdmissionController(max_inflight=2, wait_seconds=0.0)
    assert controller.acquire()
    gauge = meter.instruments["seocho.retrieval.admission.available_permits"]
    assert gauge.calls[-1] == ("set", 1, {"source": "graph"})
    assert controller.acquire()
    assert gauge.calls[-1] == ("set", 0, {"source": "graph"})
    controller.release()
    assert gauge.calls[-1] == ("set", 1, {"source": "graph"})


def test_admission_rejection_does_not_move_the_gauge(meter):
    from seocho.query.query_proxy import QueryAdmissionController

    controller = QueryAdmissionController(max_inflight=1, wait_seconds=0.0)
    assert controller.acquire()
    gauge = meter.instruments["seocho.retrieval.admission.available_permits"]
    before = list(gauge.calls)
    assert not controller.acquire()
    assert gauge.calls == before
