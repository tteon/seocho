"""Multi-plan execution + RRF fusion (ADR-0100, F8) contract tests.

Pins: gate default-off, build-execute-fuse over K shapes, empty/one-plan
pass-through (never lose the single-plan result), drop-on-error, and the
fusion dedup/ranking. Uses fake builder + executor (no DB / LLM).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from seocho.query.contracts import QueryExecution, QueryPlan
from seocho.query.multi_plan import (
    DEFAULT_MULTI_HOP_SHAPES,
    execute_multi_plan,
    multi_plan_enabled,
)


def test_multi_plan_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEOCHO_MULTI_PLAN", raising=False)
    assert multi_plan_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes"])
def test_multi_plan_opt_in(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("SEOCHO_MULTI_PLAN", val)
    assert multi_plan_enabled() is True


class _FakeBuilder:
    """Returns a distinct cypher per shape; records keyed by shape."""

    def build(self, *, intent: str, **kwargs: Any) -> Tuple[str, Dict[str, Any]]:
        return f"MATCH ({intent}) RETURN n", {"shape": intent}


class _FakeExecutor:
    def __init__(self, rows_by_shape: Dict[str, List[Dict[str, Any]]]):
        self._rows = rows_by_shape

    def execute(self, plan: QueryPlan) -> QueryExecution:
        shape = plan.params.get("shape")
        rows = self._rows.get(shape)
        if rows is None:
            return QueryExecution(cypher=plan.cypher, params=plan.params, records=[], error="boom")
        return QueryExecution(cypher=plan.cypher, params=plan.params, records=list(rows))


def test_fuses_records_across_shapes() -> None:
    rows = {
        "relationship_lookup": [{"id": "a"}, {"id": "b"}],
        "neighbors": [{"id": "b"}, {"id": "c"}],
        "entity_lookup": [{"id": "a"}],
    }
    res = execute_multi_plan(
        builder=_FakeBuilder(), executor=_FakeExecutor(rows),
        question="q", intent_data={"anchor_entity": "X"},
    )
    ids = [r["id"] for r in res.records]
    assert set(ids) == {"a", "b", "c"}  # deduped union
    # "a" and "b" appear in 2 lists each → outrank "c"
    assert ids[0] in {"a", "b"} and ids[-1] == "c"
    assert res.fused_from == 3


def test_one_plan_is_passthrough() -> None:
    rows = {"relationship_lookup": [{"id": "x"}], "neighbors": [], "entity_lookup": []}
    res = execute_multi_plan(
        builder=_FakeBuilder(), executor=_FakeExecutor({"relationship_lookup": [{"id": "x"}], "neighbors": [], "entity_lookup": []}),
        question="q", intent_data={},
    )
    assert res.fused_from == 1
    assert res.records == [{"id": "x"}]


def test_no_records_returns_empty_not_error() -> None:
    res = execute_multi_plan(
        builder=_FakeBuilder(), executor=_FakeExecutor({"relationship_lookup": [], "neighbors": [], "entity_lookup": []}),
        question="q", intent_data={},
    )
    assert res.records == []
    assert res.fused_from == 0


def test_drop_on_execution_error_keeps_other_plans() -> None:
    # 'neighbors' shape missing from rows → executor returns error; others ok.
    rows = {"relationship_lookup": [{"id": "a"}], "entity_lookup": [{"id": "a"}]}
    res = execute_multi_plan(
        builder=_FakeBuilder(), executor=_FakeExecutor(rows),
        question="q", intent_data={},
    )
    assert res.records == [{"id": "a"}]
    # provenance records the errored shape
    errored = [c for c in res.plan_provenance if c.error]
    assert any(c.shape == "neighbors" for c in errored)


def test_max_plans_caps_shapes() -> None:
    rows = {s: [{"id": s}] for s in DEFAULT_MULTI_HOP_SHAPES}
    res = execute_multi_plan(
        builder=_FakeBuilder(), executor=_FakeExecutor(rows),
        question="q", intent_data={}, max_plans=2,
    )
    # only the first 2 shapes attempted
    assert len(res.plan_provenance) == 2


def test_to_metadata_shape() -> None:
    rows = {"relationship_lookup": [{"id": "a"}], "neighbors": [{"id": "b"}], "entity_lookup": []}
    res = execute_multi_plan(
        builder=_FakeBuilder(), executor=_FakeExecutor(rows),
        question="q", intent_data={},
    )
    md = res.to_metadata()
    assert md["multi_plan"] is True
    assert md["fused_from"] == 2
    assert md["record_count"] == 2
    assert len(md["plans"]) == 3
