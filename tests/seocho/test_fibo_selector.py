"""Regression tests for ``seocho.fibo`` slice 1 (issue ``seocho-1dm8``).

Covers:

- Catalog dependency expansion (transitive, cycle-tolerant).
- Lexical selector returns deterministic, alphabetic module order.
- Three selection outcomes: OK, LOW_CONFIDENCE, NO_MATCH.
- Round-trip determinism: same input + catalog → identical result.
"""

from __future__ import annotations

import pytest

from seocho.fibo import (
    FIBOCatalog,
    FIBOModule,
    LexicalSelector,
    SelectionPolicy,
    SelectionStatus,
)


FND = FIBOModule(
    code="FND",
    iri_prefix="https://spec.edmcouncil.org/fibo/ontology/FND/",
    summary="Foundations: parties, relationships, agreements.",
    label_index={
        "Party": "https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Parties/Party",
        "Agreement": "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Agreements/Agreement",
        "Person": "https://spec.edmcouncil.org/fibo/ontology/FND/AgentsAndPeople/People/Person",
    },
    fibo_version="2024Q3",
)

BE = FIBOModule(
    code="BE",
    iri_prefix="https://spec.edmcouncil.org/fibo/ontology/BE/",
    summary="Business entities: legal persons, corporations.",
    label_index={
        "Legal Person": "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalPerson",
        "Formal Organization": "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/FormalBusinessOrganizations/FormalOrganization",
        "Corporation": "https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/Corporations/Corporation",
    },
    depends_on=("FND",),
    fibo_version="2024Q3",
)

FBC = FIBOModule(
    code="FBC",
    iri_prefix="https://spec.edmcouncil.org/fibo/ontology/FBC/",
    summary="Financial business and commerce.",
    label_index={
        "Counterparty": "https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/FinancialServicesEntities/Counterparty",
        "Financial Instrument": "https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/FinancialServicesEntities/FinancialInstrument",
    },
    depends_on=("BE",),
    fibo_version="2024Q3",
)


@pytest.fixture
def catalog() -> FIBOCatalog:
    return FIBOCatalog.from_modules([FND, BE, FBC], fibo_version="2024Q3")


def test_catalog_rejects_duplicate_codes() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        FIBOCatalog.from_modules([FND, FND])


def test_catalog_codes_are_sorted(catalog: FIBOCatalog) -> None:
    assert catalog.codes() == ("BE", "FBC", "FND")


def test_with_dependencies_expands_transitively(catalog: FIBOCatalog) -> None:
    assert catalog.with_dependencies(["FBC"]) == ("BE", "FBC", "FND")


def test_with_dependencies_handles_cycle() -> None:
    # FND ← → BE cycle is tolerated; both resolve once.
    fnd_cycle = FIBOModule(
        code="FND",
        iri_prefix="x",
        summary="",
        label_index={"Party": "iri:Party"},
        depends_on=("BE",),
    )
    be_cycle = FIBOModule(
        code="BE",
        iri_prefix="y",
        summary="",
        label_index={"Corporation": "iri:Corp"},
        depends_on=("FND",),
    )
    catalog = FIBOCatalog.from_modules([fnd_cycle, be_cycle])
    assert catalog.with_dependencies(["BE"]) == ("BE", "FND")


def test_with_dependencies_unknown_code_raises(catalog: FIBOCatalog) -> None:
    with pytest.raises(KeyError, match="DER"):
        catalog.with_dependencies(["DER"])


def test_select_ok_returns_module_and_dependencies(catalog: FIBOCatalog) -> None:
    selector = LexicalSelector()
    result = selector.select(
        "We track every Counterparty and the underlying Financial Instrument.",
        catalog=catalog,
        policy=SelectionPolicy(min_confidence=0.1),
    )
    assert result.status is SelectionStatus.OK
    # FBC matches → expanded with BE → FND (transitive).
    assert result.modules == ("BE", "FBC", "FND")
    assert result.confidence > 0.1
    assert any("Counterparty" in iri for iri in result.candidate_iris)
    assert "FBC" in result.per_module_score


def test_select_no_match_returns_empty_status(catalog: FIBOCatalog) -> None:
    selector = LexicalSelector()
    result = selector.select(
        "the quick brown fox jumps over the lazy dog",
        catalog=catalog,
        policy=SelectionPolicy(min_confidence=0.1),
    )
    assert result.status is SelectionStatus.NO_MATCH
    assert result.modules == ()
    assert result.confidence == 0.0
    assert result.candidate_iris == ()


def test_select_low_confidence_below_threshold(catalog: FIBOCatalog) -> None:
    # Only a single label matches; saturating score = 1/(1+5) ≈ 0.17.
    # Threshold 0.5 forces LOW_CONFIDENCE.
    selector = LexicalSelector()
    result = selector.select(
        "Acme is a Corporation.",
        catalog=catalog,
        policy=SelectionPolicy(min_confidence=0.5),
    )
    assert result.status is SelectionStatus.LOW_CONFIDENCE
    assert result.modules == ()
    assert 0 < result.confidence < 0.5
    assert any("Corporation" in iri for iri in result.candidate_iris)


def test_select_is_deterministic_across_runs(catalog: FIBOCatalog) -> None:
    selector = LexicalSelector()
    text = "Counterparty Corporation Person Party"
    policy = SelectionPolicy(min_confidence=0.1)
    first = selector.select(text, catalog=catalog, policy=policy)
    second = selector.select(text, catalog=catalog, policy=policy)
    assert first == second


def test_select_module_order_is_alphabetic(catalog: FIBOCatalog) -> None:
    # FBC scores highest by raw hits but output order is alphabetic for cache stability.
    selector = LexicalSelector()
    result = selector.select(
        "Corporation Financial Instrument Counterparty Party Person",
        catalog=catalog,
        policy=SelectionPolicy(min_confidence=0.1),
    )
    assert result.status is SelectionStatus.OK
    assert list(result.modules) == sorted(result.modules)


def test_label_tokens_must_all_match() -> None:
    # "Legal Person" requires both 'legal' and 'person' in the input.
    catalog = FIBOCatalog.from_modules([BE, FND])
    selector = LexicalSelector()
    only_person = selector.select(
        "She is a Person.",
        catalog=catalog,
        policy=SelectionPolicy(min_confidence=0.1),
    )
    assert "FND" in only_person.modules  # Person matches FND
    # BE's "Legal Person" requires both tokens; "person" alone does not match BE.
    assert only_person.per_module_score.get("BE", 0.0) == 0.0
