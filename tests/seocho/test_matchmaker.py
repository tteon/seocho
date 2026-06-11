"""Tests for ontology-aware agent capability matchmaking (Sycara/RETSINA).

Demonstrates the benefit over the hardcoded keyword router: the SAME matchmaker
routes diverse tasks to the right specialist, and an ontology-scoped specialist
beats a generalist on its own concepts — something keyword matching can't do.
"""

from __future__ import annotations

from seocho.agent.matchmaker import AgentCapability, Matchmaker, TaskDescriptor


def _registry() -> Matchmaker:
    mm = Matchmaker()
    # Generic graph agents (any concept).
    mm.advertise(AgentCapability.make("lpg", handles=["lookup", "aggregation"], inputs=["question"]))
    mm.advertise(AgentCapability.make("rdf", handles=["explanation", "comparison"], inputs=["question"]))
    # A finance specialist scoped to finance concepts.
    mm.advertise(AgentCapability.make(
        "finance", handles=["lookup", "aggregation"],
        ontology_scope=["Company", "Filing", "Metric"], inputs=["question"], priority=0.0,
    ))
    # Synthesis agent needs records (not a raw question).
    mm.advertise(AgentCapability.make("answer", handles=["synthesize"], inputs=["records"]))
    return mm


def test_specialist_beats_generalist_on_its_concepts():
    mm = _registry()
    task = TaskDescriptor.make("lookup", concepts=["Company"], available_inputs=["question"])
    best = mm.match(task)
    assert best is not None and best.capability.name == "finance"
    assert any("overlap" in r for r in best.reasons)


def test_generalist_wins_outside_specialist_scope():
    mm = _registry()
    task = TaskDescriptor.make("lookup", concepts=["Person"], available_inputs=["question"])
    best = mm.match(task)
    # finance scope doesn't cover Person -> generic lpg wins.
    assert best is not None and best.capability.name == "lpg"


def test_kind_routes_to_correct_specialist():
    mm = _registry()
    assert mm.match(TaskDescriptor.make("explanation", available_inputs=["question"])).capability.name == "rdf"
    assert mm.match(TaskDescriptor.make("comparison", available_inputs=["question"])).capability.name == "rdf"


def test_inputs_gate_eligibility():
    mm = _registry()
    # synthesize needs records; a question-only task can't run it -> no match.
    assert mm.match(TaskDescriptor.make("synthesize", available_inputs=["question"])) is None
    # with records available, answer agent is selected.
    best = mm.match(TaskDescriptor.make("synthesize", available_inputs=["records"]))
    assert best is not None and best.capability.name == "answer"


def test_unknown_kind_has_no_match():
    mm = _registry()
    assert mm.match(TaskDescriptor.make("teleport", available_inputs=["question"])) is None


def test_rank_orders_by_score_then_name():
    mm = _registry()
    task = TaskDescriptor.make("lookup", concepts=["Metric"], available_inputs=["question"])
    ranked = mm.rank(task)
    names = [m.capability.name for m in ranked]
    # finance (scope overlap) ranks above generic lpg; both eligible.
    assert names[0] == "finance" and "lpg" in names
    assert ranked[0].score > ranked[1].score


def test_priority_breaks_ties_between_generalists():
    mm = Matchmaker()
    mm.advertise(AgentCapability.make("a", handles=["lookup"], inputs=["question"], priority=0.0))
    mm.advertise(AgentCapability.make("b", handles=["lookup"], inputs=["question"], priority=1.0))
    best = mm.match(TaskDescriptor.make("lookup", available_inputs=["question"]))
    assert best.capability.name == "b"  # higher priority wins the tie
