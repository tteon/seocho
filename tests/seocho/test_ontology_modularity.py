"""Keet's modularisation metrics (§11.3, Table 11.1).

Three of the bands run against intuition and are pinned here because a reader
who assumes bigger-is-better reads the whole report backwards:

    cohesion        SMALL is good — a module whose entities are all wired to
                    each other cannot be decomposed further
    relative size   SMALL to MEDIUM is good — a module holding most of the
                    ontology has not modularised anything
    coupling        SMALL is good
    encapsulation   LARGE is good, and independence is defined from it
"""

from __future__ import annotations

from seocho import NodeDef, Ontology, P, RelDef
from seocho.ontology_modularity import analyse, band, findings, partition


def _two_module_ontology() -> Ontology:
    """Two taxonomies, one edge between them, one edge inside each."""
    return Ontology(
        name="two",
        nodes={
            "Animal": NodeDef(properties={"name": P(str, unique=True)}),
            "Dog": NodeDef(properties={"name": P(str, unique=True)}, broader=["Animal"]),
            "Place": NodeDef(properties={"name": P(str, unique=True)}),
            "City": NodeDef(properties={"name": P(str, unique=True)}, broader=["Place"]),
        },
        relationships={
            "PARENT_OF": RelDef("Animal", "Dog"),
            "PART_OF": RelDef("City", "Place"),
            "LIVES_IN": RelDef("Dog", "City"),
        },
    )


def test_taxonomy_roots_become_modules():
    ontology = _two_module_ontology()
    grouped, source = partition(ontology)
    assert source == "taxonomy_roots"
    assert set(grouped) == {"Animal", "Place"}
    assert set(grouped["Animal"]) == {"Animal", "Dog"}


def test_a_flat_ontology_reports_itself_as_unmodularised():
    """One module per class is the correct answer, not a measurement defect."""
    flat = Ontology(name="flat", nodes={
        "A": NodeDef(properties={"n": P(str)}),
        "B": NodeDef(properties={"n": P(str)}),
    })
    report = analyse(flat)
    assert report.partition_source == "flat_one_module_per_class"
    messages = [f["message"] for f in findings(report)]
    assert any("unmodularised" in m for m in messages)


def test_cross_module_edges_lower_encapsulation():
    """Keet 11.14: encapsulation falls as a module's axioms reach outside it."""
    report = analyse(_two_module_ontology())
    animal = next(m for m in report.modules if m.name == "Animal")
    # one internal edge (PARENT_OF) and one external (LIVES_IN)
    assert abs(animal.encapsulation - 0.5) < 1e-9


def test_independence_requires_both_full_encapsulation_and_zero_coupling():
    """Keet 11.15 — either condition alone is not enough."""
    isolated = Ontology(
        name="iso",
        nodes={"A": NodeDef(properties={"n": P(str, unique=True)}),
               "B": NodeDef(properties={"n": P(str, unique=True)}, broader=["A"])},
        relationships={"R": RelDef("A", "B")},
    )
    report = analyse(isolated)
    module = report.modules[0]
    assert module.coupling == 0.0 and module.encapsulation == 1.0
    assert module.independent is True

    coupled = next(m for m in analyse(_two_module_ontology()).modules
                   if m.name == "Animal")
    assert coupled.independent is False


def test_relative_size_flags_a_module_that_swallowed_the_ontology():
    big = Ontology(
        name="big",
        nodes={"Root": NodeDef(properties={"n": P(str)}),
               "A": NodeDef(properties={"n": P(str)}, broader=["Root"]),
               "B": NodeDef(properties={"n": P(str)}, broader=["Root"])},
    )
    messages = [f["message"] for f in findings(analyse(big))]
    assert any("has not modularised anything" in m for m in messages)


def test_redundancy_counts_duplicated_membership():
    """Keet 11.13 — the same class living in more than one module."""
    ontology = _two_module_ontology()
    report = analyse(ontology, modules={"M1": ["Animal", "Dog"],
                                        "M2": ["Dog", "City"]})
    assert report.redundancy > 0, "Dog is in both modules"


def test_attribute_and_inheritance_richness_are_per_class_averages():
    """Keet 11.16 and 11.17."""
    report = analyse(_two_module_ontology())
    animal = next(m for m in report.modules if m.name == "Animal")
    assert animal.attribute_richness == 1.0     # one property each over two classes
    assert animal.inheritance_richness == 0.5   # Dog has a parent, Animal does not


def test_the_four_point_scale_matches_table_11_1():
    assert band(0.10) == "small"
    assert band(0.40) == "medium"
    assert band(0.60) == "moderate"
    assert band(0.90) == "large"
    assert band(None) == "-"


def test_an_empty_ontology_yields_an_empty_report():
    report = analyse(Ontology(name="empty"))
    assert report.modules == [] and report.total_entities == 0
