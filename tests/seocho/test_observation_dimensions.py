"""Richer Observation dimensions: segment / basis / restatement (seocho-8kt.4).

The full reported-figure model is (entity, concept, period, unit, basis,
segment, restatement). These assert the new dimensions are keyed in AND that
the default (consolidated / not-restated) case keeps its EXISTING obs_id so
already-ingested data is not fragmented.
"""

from seocho.semantic_layer.keys import observation_key
from seocho.semantic_layer.slots import ObservationSlots

_BASE = dict(entity_key="0000320193", concept_id="metric:Revenue",
             period_key="fiscal:2023:FY", unit="USD")


def test_default_obs_id_is_backward_compatible():
    # baseline captured from the pre-8kt.4 6-part key — must not change, or
    # every already-ingested consolidated observation would re-fragment.
    assert observation_key(**_BASE) == "obs:41ced61e2daafd4e61fca43f"


def test_explicit_consolidated_defaults_match_bare_call():
    assert observation_key(**_BASE) == observation_key(
        **_BASE, segment="consolidated", is_restated=False)


def test_segment_changes_the_key():
    base = observation_key(**_BASE)
    seg = observation_key(**_BASE, segment="Footwear")
    assert seg != base
    # segment is case-insensitive / whitespace-normalized
    assert seg == observation_key(**_BASE, segment="  footwear ")


def test_restatement_changes_the_key():
    assert observation_key(**_BASE, is_restated=True) != observation_key(**_BASE)


def test_segment_and_restatement_compose_distinctly():
    keys = {
        observation_key(**_BASE),
        observation_key(**_BASE, segment="Footwear"),
        observation_key(**_BASE, is_restated=True),
        observation_key(**_BASE, segment="Footwear", is_restated=True),
    }
    assert len(keys) == 4


def test_observation_slots_thread_the_dimensions():
    s = ObservationSlots(entity_cik="0000320193", concept_id="metric:Revenue",
                         period_keys=("fiscal:2023:FY",), segment="Footwear",
                         is_restated=True)
    assert s.observation_keys() == (
        observation_key(**_BASE, segment="Footwear", is_restated=True),
    )
    # default slots reproduce the backward-compatible key
    d = ObservationSlots(entity_cik="0000320193", concept_id="metric:Revenue",
                         period_keys=("fiscal:2023:FY",))
    assert d.observation_keys() == ("obs:41ced61e2daafd4e61fca43f",)
