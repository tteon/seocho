"""Execution results must remain ordinary JSON data with or without envelopes."""

from __future__ import annotations

import json
from typing import Any

import pytest

from seocho.models import ExecutionResult, SemanticRunResponse


@pytest.mark.parametrize(
    ("explicit", "envelope", "expected"),
    [
        ({}, {}, {}),
        ({}, {"id": "envelope"}, {"id": "envelope"}),
        ({"id": "explicit"}, {}, {"id": "explicit"}),
        ({"id": "explicit"}, {"id": "envelope"}, {"id": "explicit"}),
    ],
)
def test_pattern_constructor_and_json(
    explicit: dict[str, Any],
    envelope: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    result = ExecutionResult(
        requested_style="direct",
        runtime_mode="semantic",
        response="ok",
        agent_pattern=explicit,
        answer_envelope={"agent_pattern": envelope},
    )
    assert result.agent_pattern == expected
    assert json.loads(json.dumps(result.to_dict()))["agent_pattern"] == expected


def test_default_pattern_is_independent_json_data() -> None:
    first = ExecutionResult("direct", "semantic", "ok")
    second = ExecutionResult("direct", "semantic", "ok")
    first.agent_pattern["id"] = "first"
    assert json.loads(json.dumps(second.to_dict()))["agent_pattern"] == {}


def test_envelope_pattern_is_copied_at_construction() -> None:
    pattern = {"id": "original"}
    result = ExecutionResult(
        "direct", "semantic", "ok", answer_envelope={"agent_pattern": pattern}
    )
    pattern["id"] = "changed"
    assert result.agent_pattern == {"id": "original"}


def test_from_run_result_preserves_pattern_in_both_surfaces() -> None:
    result = ExecutionResult.from_run_result(
        requested_style="direct",
        runtime_mode="semantic",
        resolved_targets=[],
        result=SemanticRunResponse(
            response="ok", route="lpg", agent_pattern={"id": "semantic"}
        ),
    )
    payload = json.loads(json.dumps(result.to_dict()))
    assert (
        payload["agent_pattern"]
        == payload["answer_envelope"]["agent_pattern"]
        == {"id": "semantic"}
    )
