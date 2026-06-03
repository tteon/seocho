"""RouteProfile route-conditional planner selection (opik exp5-derived).

Pins the core empirical rule: multi-step planner is reserved for
multi-hop/compositional questions; simple lookups stay on the cheap
single-pass template. From icml exp5 (planner beats single_call ONLY on
the multi-hop bucket).
"""

from __future__ import annotations

import pytest

from seocho.query.route_profile import (
    Planner,
    ToolPolicy,
    classify_route_class,
    planner_exec_params,
    select_route_profile,
    ROUTE_CATALOG,
)


@pytest.mark.parametrize(
    ("reasoning_type", "expected_class"),
    [
        ("single_hop", "lookup"),
        ("numeric_lookup", "lookup"),
        ("compositional", "multi_hop"),
        ("multi-hop", "multi_hop"),
    ],
)
def test_reasoning_type_label_takes_precedence(reasoning_type, expected_class) -> None:
    # curated dataset label wins over text rules
    assert classify_route_class("anything at all", reasoning_type=reasoning_type) == expected_class


def test_text_rule_escalates_compositional_wording() -> None:
    assert classify_route_class("What is X as a fraction of Y?") == "multi_hop"
    assert classify_route_class("Compare A versus B across 2022 and 2023") == "multi_hop"


def test_text_rule_keeps_lookup_cheap() -> None:
    assert classify_route_class("Where is Apple headquartered?") == "lookup"
    assert classify_route_class("What was Microsoft's total revenue in 2023?") == "lookup"


def test_empty_question_is_default_route() -> None:
    assert classify_route_class("") == "entity_summary"


def test_only_multi_hop_escalates_to_multi_step_planner() -> None:
    """The decisive exp5 rule: MULTI_STEP planner is reserved for multi_hop.
    Every other route stays on a single-pass planner."""
    assert ROUTE_CATALOG["multi_hop"].planner == Planner.MULTI_STEP
    for route, prof in ROUTE_CATALOG.items():
        if route != "multi_hop":
            assert prof.planner != Planner.MULTI_STEP, f"{route} should not use multi_step"


def test_planner_exec_params_costs() -> None:
    # multi_step = reasoning + repair budget (expensive)
    assert planner_exec_params(Planner.MULTI_STEP) == {"reasoning_mode": True, "repair_budget": 2}
    # template = single pass, no repair (cheap)
    assert planner_exec_params(Planner.TEMPLATE) == {"reasoning_mode": False, "repair_budget": 0}
    # cost_ranked = single pass, modest repair
    assert planner_exec_params(Planner.COST_RANKED)["reasoning_mode"] is False


def test_select_route_profile_compositional_gets_multi_step() -> None:
    prof = select_route_profile("Tesla automotive revenue share", reasoning_type="compositional")
    assert prof.route_class == "multi_hop"
    assert prof.planner == Planner.MULTI_STEP
    assert prof.tool_policy == ToolPolicy.RETRIEVE_VERIFY_CYPHER


def test_select_route_profile_lookup_stays_template() -> None:
    prof = select_route_profile("Who is the CEO of Amazon?", reasoning_type="single_hop")
    assert prof.route_class == "lookup"
    assert prof.planner == Planner.TEMPLATE
    # the cheap single-pass levers
    assert planner_exec_params(prof.planner)["repair_budget"] == 0
