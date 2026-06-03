"""Unit tests for semantic_decompose (ADR-0103 S6) — pure parts, no LLM.

Covers QuerySlots JSON parsing/validation, surface→canonical slot resolution
(closed concept registry + entity→CIK + period normalization), and the
unresolved-slot accounting the arbiter (S5) routes on. The live MARA decompose
is exercised by scripts/benchmarks/sra_probe.py, not here.
"""

from __future__ import annotations

from seocho.query.semantic_decompose import (
    QuerySlots,
    parse_slots,
    resolve_slots,
)
from seocho.semantic_layer import default_registry, default_resolver


# ---- parse_slots ------------------------------------------------------------

def test_parse_slots_valid_json():
    qs = parse_slots('{"intent":"metric_lookup","metric_surface":"total revenue",'
                     '"entity_surface":"Apple Inc.","period":"FY2024","aggregation":"none"}')
    assert qs == QuerySlots("metric_lookup", "total revenue", "Apple Inc.", "FY2024", "none")


def test_parse_slots_tolerates_surrounding_prose():
    qs = parse_slots('Here you go:\n{"intent":"metric_lookup","metric_surface":"net income",'
                     '"entity_surface":"MSFT","period":"FY2023"}  done')
    assert qs is not None and qs.metric_surface == "net income"
    assert qs.aggregation == "none"          # default when omitted


def test_parse_slots_rejects_bad_intent_and_garbage():
    assert parse_slots('{"intent":"frobnicate","metric_surface":"x"}') is None
    assert parse_slots("not json at all") is None
    assert parse_slots("") is None


# ---- resolve_slots ----------------------------------------------------------

def _reg_res():
    return default_registry(), default_resolver()


def test_resolve_slots_full_resolution():
    reg, res = _reg_res()
    qs = QuerySlots("metric_lookup", "total revenue", "Apple Inc.", "FY2024")
    slots = resolve_slots(qs, registry=reg, resolver=res)
    assert slots.is_fully_resolved
    assert slots.concept_id == "metric:Revenue"
    assert slots.entity_cik == "0000320193"
    assert slots.period_keys == ("fiscal:2024:FY",)
    assert slots.unresolved == ()


def test_resolve_slots_net_income_alias_and_ticker():
    reg, res = _reg_res()
    qs = QuerySlots("metric_lookup", "net income", "AAPL", "fiscal year 2023")
    slots = resolve_slots(qs, registry=reg, resolver=res)
    assert slots.concept_id == "metric:NetIncome"
    assert slots.entity_cik == "0000320193"
    assert slots.period_keys == ("fiscal:2023:FY",)


def test_resolve_slots_records_unresolved():
    reg, res = _reg_res()
    qs = QuerySlots("metric_lookup", "gross margin", "Nonexistent Co", "last year")
    slots = resolve_slots(qs, registry=reg, resolver=res)
    assert not slots.is_fully_resolved
    assert set(slots.unresolved) == {"concept", "entity", "period"}


def test_resolve_slots_partial_unresolved_period():
    reg, res = _reg_res()
    qs = QuerySlots("metric_lookup", "revenue", "Apple", "")
    slots = resolve_slots(qs, registry=reg, resolver=res)
    assert slots.concept_id == "metric:Revenue"
    assert slots.entity_cik == "0000320193"
    assert slots.unresolved == ("period",)
