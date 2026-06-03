"""Unit tests for the semantic-layer package (ADR-0103, slice S1).

Covers the four shared contracts the writer and reader will both depend on:
deterministic observation_key, closed concept vocabulary, canonical period
normalization, entity→CIK resolution, and the resolved-slots shape.
"""

from __future__ import annotations

from seocho.semantic_layer import (
    ConceptRegistry,
    EntityResolver,
    MetricConcept,
    ObservationSlots,
    default_registry,
    default_resolver,
    normalize_name,
    normalize_period,
    observation_key,
    parse_period,
)


# ---- observation_key: determinism + sensitivity -----------------------------

def test_observation_key_is_deterministic():
    a = observation_key(entity_key="0000320193", concept_id="metric:Revenue",
                        period_key="fiscal:2024:FY", unit="USD")
    b = observation_key(entity_key="0000320193", concept_id="metric:Revenue",
                        period_key="fiscal:2024:FY", unit="USD")
    assert a == b
    assert a.startswith("obs:")


def test_observation_key_changes_with_each_component():
    base = dict(entity_key="0000320193", concept_id="metric:Revenue",
                period_key="fiscal:2024:FY", unit="USD")
    k = observation_key(**base)
    assert observation_key(**{**base, "period_key": "fiscal:2023:FY"}) != k
    assert observation_key(**{**base, "concept_id": "metric:NetIncome"}) != k
    assert observation_key(**{**base, "entity_key": "0000789019"}) != k
    assert observation_key(**{**base, "workspace_id": "ws-2"}) != k


def test_observation_key_normalizes_unit_and_basis_case():
    assert observation_key(entity_key="c", concept_id="m", period_key="p",
                           unit="usd", basis="Consolidated") == \
           observation_key(entity_key="c", concept_id="m", period_key="p",
                           unit="USD", basis="consolidated")


# ---- ConceptRegistry: closed vocabulary -------------------------------------

def test_concept_registry_resolves_aliases_to_canonical_id():
    reg = default_registry()
    assert reg.resolve("revenue") == "metric:Revenue"
    assert reg.resolve("Net Sales") == "metric:Revenue"      # alias, case-insensitive
    assert reg.resolve("  TOPLINE ") == "metric:Revenue"     # whitespace + case
    assert reg.resolve("net earnings") == "metric:NetIncome"
    assert reg.resolve("nonsense metric") is None            # out of vocabulary


def test_concept_registry_membership_and_get():
    reg = default_registry()
    assert reg.is_member("metric:Revenue")
    assert not reg.is_member("metric:Unknown")
    assert reg.get("metric:NetIncome").unit_class == "currency"
    assert "revenue" in reg.candidate_surfaces           # grounding candidate set


def test_concept_registry_custom_set():
    reg = ConceptRegistry((MetricConcept("metric:EPS", "EPS",
                                         ("earnings per share",), "ratio"),))
    assert reg.resolve("earnings per share") == "metric:EPS"
    assert reg.resolve("revenue") is None


# ---- period normalization ---------------------------------------------------

def test_normalize_period_fiscal_year_forms():
    for raw in ("FY2024", "fiscal 2024", "FY 2024", "2024", "in fiscal year 2024"):
        assert normalize_period(raw) == "fiscal:2024:FY", raw


def test_normalize_period_quarter():
    assert normalize_period("Q3 2024") == "fiscal:2024:Q3"
    assert normalize_period("third quarter 2024") == "fiscal:2024:Q3"


def test_normalize_period_calendar_basis():
    assert normalize_period("calendar 2023") == "calendar:2023:FY"


def test_normalize_period_unparseable():
    assert normalize_period("last year") is None
    assert normalize_period("") is None


def test_parse_period_typed():
    p = parse_period("Q4 2025")
    assert (p.fiscal_year, p.fiscal_period, p.basis) == (2025, "Q4", "fiscal")
    assert p.key == "fiscal:2025:Q4"


# ---- entity → CIK resolution ------------------------------------------------

def test_entity_resolver_ticker_and_name():
    r = default_resolver()
    assert r.resolve("AAPL") == "0000320193"
    assert r.resolve("aapl") == "0000320193"               # case-insensitive ticker
    assert r.resolve("Apple Inc.") == "0000320193"         # name with suffix
    assert r.resolve("Apple") == "0000320193"              # normalized name
    assert r.resolve("Unknown Co") is None


def test_normalize_name_strips_suffixes():
    assert normalize_name("Apple Inc.") == "apple"
    assert normalize_name("NVIDIA Corp") == "nvidia"
    assert normalize_name("Amazon.com, Inc.") == "amazon com"


def test_entity_resolver_from_ticker_map_zero_pads():
    r = EntityResolver.from_ticker_map({"foo": "123"}, {"foo": "Foo Holdings"})
    assert r.resolve("FOO") == "0000000123"
    assert r.resolve("Foo") == "0000000123"                # suffix 'holdings' stripped


# ---- ObservationSlots: resolution state + key derivation --------------------

def test_observation_slots_full_resolution_and_keys():
    s = ObservationSlots(entity_cik="0000320193", concept_id="metric:Revenue",
                         period_keys=("fiscal:2024:FY", "fiscal:2023:FY"))
    assert s.is_fully_resolved
    keys = s.observation_keys()
    assert len(keys) == 2 and all(k.startswith("obs:") for k in keys)
    # keys match the standalone function (writer/reader share the same derivation)
    assert keys[0] == observation_key(entity_key="0000320193",
                                      concept_id="metric:Revenue",
                                      period_key="fiscal:2024:FY", unit="USD")


def test_observation_slots_unresolved_blocks_full_resolution():
    s = ObservationSlots(entity_cik="0000320193", concept_id="metric:Revenue",
                         period_keys=("fiscal:2024:FY",), unresolved=("period",))
    assert not s.is_fully_resolved
    s2 = ObservationSlots(concept_id="metric:Revenue", period_keys=("fiscal:2024:FY",))
    assert not s2.is_fully_resolved                        # missing entity_cik
