"""Actionable guardrail rejections (ADR-0169 follow-up, seocho-6md).

A rejection must tell the model HOW to fix the query, not just WHAT failed.
The measured failure (ADR-0169) was a model flailing 6 turns rewriting the
*query* when the fix was a *params_json* field it never saw named.
"""

from __future__ import annotations

import pytest

pytest.importorskip("agents", reason="openai-agents SDK not installed")

from seocho.integrations.openai_agents import (  # noqa: E402
    _VIOLATION_HINTS, _actionable_reason)


def test_result_limit_hint_points_at_params_json():
    """The flail's root cause: the fix is a params_json field, named explicitly."""
    msg = _actionable_reason("result_limit_exceeded", max_rows=50)
    assert "params_json" in msg
    assert "limit" in msg
    assert "50" in msg                       # the actual cap, not a placeholder


def test_workspace_scope_hint_shows_the_inline_form():
    msg = _actionable_reason("missing_workspace_scope_expression", max_rows=50)
    assert "{_workspace_id: $workspace_id}" in msg
    assert "WHERE" in msg                     # states the WHERE form does NOT satisfy it


def test_payload_is_interpolated():
    assert "Widget" in _actionable_reason("unknown_labels:Widget", max_rows=50)
    assert "OWNS" in _actionable_reason("unknown_relationships:OWNS", max_rows=50)


def test_unknown_violation_kind_falls_through_unchanged():
    assert _actionable_reason("some_new_token:x", max_rows=50) == "some_new_token:x"


@pytest.mark.parametrize("kind", [
    "forbidden_token", "missing_return_clause", "missing_parameterized_limit",
    "missing_parameter", "missing_parameter_value", "unbounded_graph_path",
    "graph_hop_limit_exceeded", "unknown_labels", "unknown_relationships",
    "unknown_properties", "missing_workspace_scope_expression",
    "result_limit_exceeded",
])
def test_every_validator_violation_kind_has_a_hint(kind):
    """Coverage: every deterministic violation the validator can emit maps to a
    concrete instruction, so no rejection is left opaque."""
    assert kind in _VIOLATION_HINTS
    msg = _actionable_reason(kind, max_rows=50)
    assert msg != kind and "—" in msg        # kind + a how-to-fix clause
