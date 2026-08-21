"""Unit tests for the ontology arbiter (ADR-0103 S5) — pure decision table.

The arbiter MEASURES (resolved slots + an injected graph probe) and emits a
routing HINT; it makes no routing decision. Tests inject a fake probe_fn so no
graph is needed, then cover make_graph_probe against a fake store.
"""

from __future__ import annotations

from seocho.query.arbiter import (
    CLARIFY,
    FAIL,
    GraphProbe,
    NARRATIVE,
    STRUCTURED,
    arbitrate,
    make_graph_probe,
)
from seocho.semantic_layer import ObservationSlots


def _resolved(periods=("fiscal:2024:FY",)):
    return ObservationSlots(entity_cik="0000320193", concept_id="metric:Revenue",
                            period_keys=periods)


def _probe_with(available):
    return lambda slots: GraphProbe(entity_has_concept=bool(available),
                                    available_periods=tuple(available))


# ---- decision table ---------------------------------------------------------

def test_structured_when_resolved_and_period_present():
    hint = arbitrate(_resolved(), probe_fn=_probe_with(["fiscal:2024:FY", "fiscal:2023:FY"]))
    assert hint.route == STRUCTURED
    assert hint.graph_has_data and hint.ontology_id == "finance"
    assert hint.concept_id == "metric:Revenue"


def test_clarify_when_requested_period_absent_in_graph():
    hint = arbitrate(_resolved(("fiscal:2099:FY",)), probe_fn=_probe_with(["fiscal:2024:FY"]))
    assert hint.route == CLARIFY
    assert hint.graph_has_data
    assert hint.available_periods == ("fiscal:2024:FY",)


def test_narrative_when_graph_lacks_entity_concept():
    hint = arbitrate(_resolved(), probe_fn=_probe_with([]))   # probe: no observations
    assert hint.route == NARRATIVE
    assert hint.graph_has_data is False


def test_clarify_when_period_unresolved():
    slots = ObservationSlots(entity_cik="0000320193", concept_id="metric:Revenue",
                             period_keys=(), unresolved=("period",))
    hint = arbitrate(slots, probe_fn=_probe_with(["fiscal:2024:FY"]))
    assert hint.route == CLARIFY
    assert "period" in hint.missing_slots


def test_narrative_when_concept_or_entity_out_of_vocab():
    oov_concept = ObservationSlots(entity_cik="0000320193", concept_id="",
                                   period_keys=("fiscal:2024:FY",), unresolved=("concept",))
    assert arbitrate(oov_concept, probe_fn=_probe_with(["fiscal:2024:FY"])).route == NARRATIVE
    oov_entity = ObservationSlots(entity_cik="", concept_id="metric:Revenue",
                                  period_keys=("fiscal:2024:FY",), unresolved=("entity",))
    assert arbitrate(oov_entity).route == NARRATIVE     # no probe needed


def test_fail_when_decompose_failed():
    slots = ObservationSlots(unresolved=("decompose_failed",))
    assert arbitrate(slots).route == FAIL


def test_hint_to_span_is_flat():
    span = arbitrate(_resolved(), probe_fn=_probe_with(["fiscal:2024:FY"])).to_span()
    assert span["arbiter.route"] == STRUCTURED
    assert span["arbiter.concept_id"] == "metric:Revenue"
    assert "arbiter.rationale" in span


# ---- make_graph_probe against a fake store ----------------------------------

def test_make_graph_probe_reads_periods():
    class _Store:
        def query(self, cypher, params=None, database="neo4j"):
            assert "concept_id" in cypher and params["cik"] == "0000320193"
            return [{"periods": ["fiscal:2024:FY", "fiscal:2023:FY"]}]

    probe = make_graph_probe(_Store(), database="db", workspace_id="ws")
    res = probe(_resolved())
    assert res.entity_has_concept
    assert set(res.available_periods) == {"fiscal:2024:FY", "fiscal:2023:FY"}


def test_make_graph_probe_swallows_errors_and_handles_missing_slots():
    class _Boom:
        def query(self, *a, **k):
            raise RuntimeError("bolt down")

    assert make_graph_probe(_Boom())( _resolved()).entity_has_concept is False
    # no cik/concept -> no query attempted
    empty = ObservationSlots()
    assert make_graph_probe(_Boom())(empty).entity_has_concept is False


# ---- v2: multi-ontology manifest selection ----------------------------------

from seocho.query.arbiter import OntologyManifest, select_ontology
from seocho.semantic_layer import (
    ConceptRegistry, EntityResolver, MetricConcept, default_registry, default_resolver,
)


def _clinical_manifest():
    reg = ConceptRegistry((
        MetricConcept("metric:PatientCount", "Patient Count",
                      ("patients", "patient volume", "admissions"), "count"),
        MetricConcept("metric:MortalityRate", "Mortality Rate",
                      ("death rate", "mortality"), "ratio"),
    ))
    return OntologyManifest("clinical", reg, EntityResolver())


def _finance_manifest():
    return OntologyManifest("finance", default_registry(), default_resolver())


def test_select_ontology_routes_finance_vs_clinical():
    manifests = [_finance_manifest(), _clinical_manifest()]
    # exact closed-vocab membership → score 1.0 → correct ontology
    assert select_ontology("total revenue", manifests).ontology_id == "finance"
    assert select_ontology("net income", manifests).ontology_id == "finance"
    assert select_ontology("admissions", manifests).ontology_id == "clinical"
    assert select_ontology("mortality", manifests).ontology_id == "clinical"


def test_select_ontology_null_when_below_threshold():
    manifests = [_finance_manifest(), _clinical_manifest()]
    m = select_ontology("xyzzy nonsense token", manifests, threshold=0.4)
    assert m.ontology_id is None
    assert set(m.scores) == {"finance", "clinical"}   # per-ontology scores recorded


def test_select_ontology_single_manifest_is_degenerate_v1():
    m = select_ontology("revenue", [_finance_manifest()])
    assert m.ontology_id == "finance" and m.score == 1.0
