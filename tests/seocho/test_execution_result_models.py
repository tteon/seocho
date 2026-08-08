"""Regression tests for ExecutionResult.agent_pattern (seocho-crd).

A property named ``agent_pattern`` used to shadow the dataclass field of the
same name inside the class body. With ``@dataclass(slots=True)`` the property
object became the field's *default value*, so a default-constructed
``ExecutionResult`` returned a ``property`` instance instead of a dict, and
the envelope-reading property itself was never operative.
"""

from seocho.models import ExecutionResult


def _make(**kwargs) -> ExecutionResult:
    return ExecutionResult(
        requested_style="direct",
        runtime_mode="router",
        response="ok",
        **kwargs,
    )


def test_agent_pattern_defaults_to_empty_dict() -> None:
    result = _make()
    assert result.agent_pattern == {}
    assert isinstance(result.agent_pattern, dict)


def test_agent_pattern_kwarg_round_trips() -> None:
    result = _make(agent_pattern={"pattern": "semantic_direct"})
    assert result.agent_pattern["pattern"] == "semantic_direct"
