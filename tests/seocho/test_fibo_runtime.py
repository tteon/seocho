"""Regression tests for ``seocho.fibo.runtime`` (issue ``seocho-1dm8``).

Covers:

- ``run_with_fibo`` produces a locked descriptor on OK selection.
- Trace metadata shape matches the ticket contract (CLAUDE.md §9).
- Cache-key fragment is deterministic and changes on workspace, version,
  or module-set change (CLAUDE.md §18).
- Workspace propagation is enforced (CLAUDE.md §6.1).
- Audit-strict ``NO_MATCH`` refuses; permissive ``NO_MATCH`` degrades.
- ``RoutingPolicy`` and bare ``SelectionPolicy`` are both accepted.
"""

from __future__ import annotations

import pytest

from seocho.agent_config import RoutingPolicy
from seocho.fibo import (
    AUDIT_REFUSE_THRESHOLD,
    FIBOCatalog,
    FIBOModule,
    FIBOSelectionRefused,
    LexicalSelector,
    RunMode,
    SelectionPolicy,
    SelectionStatus,
    run_with_fibo,
)
from seocho.fibo.runtime import _cache_key_fragment


FND = FIBOModule(
    code="FND",
    iri_prefix="https://spec.edmcouncil.org/fibo/ontology/FND/",
    summary="Foundations.",
    label_index={
        "Party": "iri:FND/Party",
        "Agreement": "iri:FND/Agreement",
        "Person": "iri:FND/Person",
    },
    fibo_version="2024Q3",
)
BE = FIBOModule(
    code="BE",
    iri_prefix="https://spec.edmcouncil.org/fibo/ontology/BE/",
    summary="Business entities.",
    label_index={
        "Corporation": "iri:BE/Corporation",
        "Legal Person": "iri:BE/LegalPerson",
    },
    depends_on=("FND",),
    fibo_version="2024Q3",
)
FBC = FIBOModule(
    code="FBC",
    iri_prefix="https://spec.edmcouncil.org/fibo/ontology/FBC/",
    summary="Financial business and commerce.",
    label_index={
        "Counterparty": "iri:FBC/Counterparty",
        "Financial Instrument": "iri:FBC/FinancialInstrument",
    },
    depends_on=("BE",),
    fibo_version="2024Q3",
)


@pytest.fixture
def catalog() -> FIBOCatalog:
    return FIBOCatalog.from_modules([FND, BE, FBC], fibo_version="2024Q3")


def test_run_with_fibo_ok_returns_locked_descriptor(catalog: FIBOCatalog) -> None:
    descriptor = run_with_fibo(
        prompt="Acme Corporation lists every Counterparty and Financial Instrument.",
        workspace_id="ws-acme",
        catalog=catalog,
        mode=RunMode.INDEX,
        policy=SelectionPolicy(min_confidence=0.1),
    )
    assert descriptor.selection_status is SelectionStatus.OK
    assert descriptor.modules == ("BE", "FBC", "FND")
    assert descriptor.workspace_id == "ws-acme"
    assert descriptor.fibo_version == "2024Q3"
    assert descriptor.mode is RunMode.INDEX
    assert descriptor.selector_name == "lexical"
    assert descriptor.cache_key_fragment  # non-empty stable fragment


def test_trace_metadata_shape_matches_ticket_contract(catalog: FIBOCatalog) -> None:
    descriptor = run_with_fibo(
        prompt="Counterparty Corporation Party",
        workspace_id="ws-1",
        catalog=catalog,
        mode=RunMode.QUERY,
        policy=SelectionPolicy(min_confidence=0.1),
    )
    meta = descriptor.to_trace_metadata()
    # Ticket scope (4): {fibo_modules, fibo_version, selector_kind, selection_confidence}.
    for required_key in (
        "fibo_modules",
        "fibo_version",
        "selector_kind",
        "selection_confidence",
        "selection_status",
        "candidate_iris",
        "workspace_id",
        "mode",
        "cache_key_fragment",
    ):
        assert required_key in meta
    assert meta["mode"] == "query"
    assert meta["fibo_version"] == "2024Q3"
    assert isinstance(meta["fibo_modules"], list)


def test_cache_key_fragment_is_deterministic(catalog: FIBOCatalog) -> None:
    a = run_with_fibo(
        prompt="Counterparty",
        workspace_id="ws-1",
        catalog=catalog,
        mode=RunMode.INDEX,
        policy=SelectionPolicy(min_confidence=0.1),
    )
    b = run_with_fibo(
        prompt="Counterparty",
        workspace_id="ws-1",
        catalog=catalog,
        mode=RunMode.INDEX,
        policy=SelectionPolicy(min_confidence=0.1),
    )
    assert a.cache_key_fragment == b.cache_key_fragment


def test_cache_key_fragment_changes_on_workspace(catalog: FIBOCatalog) -> None:
    ws_a = _cache_key_fragment(
        workspace_id="ws-a", fibo_version="2024Q3", modules=("BE", "FND")
    )
    ws_b = _cache_key_fragment(
        workspace_id="ws-b", fibo_version="2024Q3", modules=("BE", "FND")
    )
    assert ws_a != ws_b


def test_cache_key_fragment_changes_on_version(catalog: FIBOCatalog) -> None:
    v_a = _cache_key_fragment(
        workspace_id="ws", fibo_version="2024Q3", modules=("BE",)
    )
    v_b = _cache_key_fragment(
        workspace_id="ws", fibo_version="2024Q4", modules=("BE",)
    )
    assert v_a != v_b


def test_cache_key_fragment_changes_on_modules() -> None:
    a = _cache_key_fragment(
        workspace_id="ws", fibo_version="v", modules=("BE",)
    )
    b = _cache_key_fragment(
        workspace_id="ws", fibo_version="v", modules=("BE", "FND")
    )
    assert a != b


def test_cache_key_fragment_is_module_order_insensitive() -> None:
    a = _cache_key_fragment(
        workspace_id="ws", fibo_version="v", modules=("BE", "FND")
    )
    b = _cache_key_fragment(
        workspace_id="ws", fibo_version="v", modules=("FND", "BE")
    )
    assert a == b


def test_workspace_id_required(catalog: FIBOCatalog) -> None:
    with pytest.raises(ValueError, match="workspace_id"):
        run_with_fibo(
            prompt="Corporation",
            workspace_id="",
            catalog=catalog,
            mode=RunMode.INDEX,
        )
    with pytest.raises(ValueError, match="workspace_id"):
        run_with_fibo(
            prompt="Corporation",
            workspace_id="   ",
            catalog=catalog,
            mode=RunMode.INDEX,
        )


def test_no_match_with_audit_strict_refuses(catalog: FIBOCatalog) -> None:
    strict = RoutingPolicy(
        latency=0.3,
        token_efficiency=0.3,
        information_quality=0.4,
        audit_strictness=0.9,
    )
    with pytest.raises(FIBOSelectionRefused) as exc_info:
        run_with_fibo(
            prompt="the quick brown fox",
            workspace_id="ws-1",
            catalog=catalog,
            mode=RunMode.QUERY,
            policy=strict,
        )
    assert exc_info.value.workspace_id == "ws-1"
    assert exc_info.value.audit_strictness == 0.9


def test_no_match_with_audit_permissive_returns_empty(catalog: FIBOCatalog) -> None:
    permissive = RoutingPolicy(audit_strictness=0.3)
    descriptor = run_with_fibo(
        prompt="the quick brown fox",
        workspace_id="ws-1",
        catalog=catalog,
        mode=RunMode.QUERY,
        policy=permissive,
    )
    assert descriptor.selection_status is SelectionStatus.NO_MATCH
    assert descriptor.modules == ()
    assert descriptor.selection_confidence == 0.0


def test_audit_threshold_boundary_is_inclusive(catalog: FIBOCatalog) -> None:
    # AUDIT_REFUSE_THRESHOLD is the inclusive lower bound for refusal.
    assert AUDIT_REFUSE_THRESHOLD == 0.7
    at_threshold = RoutingPolicy(audit_strictness=AUDIT_REFUSE_THRESHOLD)
    with pytest.raises(FIBOSelectionRefused):
        run_with_fibo(
            prompt="nothing matches",
            workspace_id="ws",
            catalog=catalog,
            mode=RunMode.INDEX,
            policy=at_threshold,
        )


def test_routing_policy_to_selection_policy_high_coverage_lowers_threshold() -> None:
    low = RoutingPolicy(fibo_coverage=0.0).to_selection_policy()
    high = RoutingPolicy(fibo_coverage=1.0).to_selection_policy()
    assert high.min_confidence < low.min_confidence


def test_routing_policy_to_selection_policy_passes_audit_strictness() -> None:
    sel = RoutingPolicy(audit_strictness=0.85).to_selection_policy()
    assert sel.audit_strictness == 0.85


def test_routing_policy_validates_new_axes() -> None:
    with pytest.raises(ValueError, match="fibo_coverage"):
        RoutingPolicy(fibo_coverage=1.5)
    with pytest.raises(ValueError, match="audit_strictness"):
        RoutingPolicy(audit_strictness=-0.1)


def test_unsupported_policy_type_rejected(catalog: FIBOCatalog) -> None:
    with pytest.raises(TypeError, match="unsupported policy type"):
        run_with_fibo(
            prompt="Corporation",
            workspace_id="ws",
            catalog=catalog,
            mode=RunMode.INDEX,
            policy="strict",  # type: ignore[arg-type]
        )


def test_default_policy_is_permissive(catalog: FIBOCatalog) -> None:
    # No policy passed → SelectionPolicy() defaults; NO_MATCH degrades.
    descriptor = run_with_fibo(
        prompt="zzz nothing here",
        workspace_id="ws",
        catalog=catalog,
        mode=RunMode.INDEX,
    )
    assert descriptor.selection_status is SelectionStatus.NO_MATCH
    assert descriptor.modules == ()


def test_descriptor_carries_per_module_scores(catalog: FIBOCatalog) -> None:
    descriptor = run_with_fibo(
        prompt="Counterparty Corporation Party Person",
        workspace_id="ws",
        catalog=catalog,
        mode=RunMode.INDEX,
        policy=SelectionPolicy(min_confidence=0.1),
    )
    assert "FBC" in descriptor.per_module_score
    assert "BE" in descriptor.per_module_score
    assert "FND" in descriptor.per_module_score


def test_default_selector_is_lexical(catalog: FIBOCatalog) -> None:
    descriptor = run_with_fibo(
        prompt="Corporation Party",
        workspace_id="ws",
        catalog=catalog,
        mode=RunMode.INDEX,
        policy=SelectionPolicy(min_confidence=0.1),
    )
    assert descriptor.selector_name == "lexical"


def test_explicit_selector_is_honored(catalog: FIBOCatalog) -> None:
    descriptor = run_with_fibo(
        prompt="Corporation",
        workspace_id="ws",
        catalog=catalog,
        mode=RunMode.INDEX,
        selector=LexicalSelector(name="lexical-v2", score_scale=3),
        policy=SelectionPolicy(min_confidence=0.1),
    )
    assert descriptor.selector_name == "lexical-v2"
