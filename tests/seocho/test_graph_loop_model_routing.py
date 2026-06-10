"""Wiring the cost-aware model router into the ReAct loop (seocho-t8m).

Demonstrates the benefit: across repair iterations the loop escalates the model
tier (FAST -> BALANCED -> FRONTIER) instead of retrying the same model — and
when no router is configured, no model is forced (behaviour preserved).
"""

from __future__ import annotations

from seocho.agent.graph_loop import GraphAgenticLoop
from seocho.routing import ModelRouter


class _RecordingClient:
    """Records the model each ask() receives; returns differing medium answers
    so the loop keeps improving (and thus escalating) without early-stopping."""

    def __init__(self):
        self.models = []
        self._n = 0

    def ask(self, question, *, model=None, reasoning_mode=False, repair_budget=0):
        self.models.append(model)
        self._n += 1
        # medium-length, distinct each call -> confidence "medium", no "no_change" stop
        return (
            f"This is a medium-length partial answer number {self._n} with enough "
            "text to score as medium confidence in the loop evaluator."
        )


def _augment_lookup(_q):
    return {"intent": {"intent": "lookup", "confidence": 0.9}}


def test_loop_escalates_model_tier_on_repair():
    client = _RecordingClient()
    loop = GraphAgenticLoop(
        client,
        model_router=ModelRouter.mara_default(),
        max_iterations=3,
        augment_fn=_augment_lookup,
        enable_analytics=False,
    )
    loop.run("what is acme's revenue")
    # intent=lookup -> FAST base; each repair escalates one tier.
    assert client.models[0] == "DeepSeek-V3.1"          # FAST  (escalate 0)
    assert client.models[1] == "MiniMax-M2.5"           # BALANCED (escalate 1)
    if len(client.models) >= 3:
        assert client.models[2] == "MiniMax-M2.7"       # FRONTIER (escalate 2)
    # monotonic, never de-escalates
    assert client.models == sorted(
        client.models, key=["DeepSeek-V3.1", "MiniMax-M2.5", "MiniMax-M2.7"].index
    )


def test_no_router_forces_no_model_preserving_behavior():
    client = _RecordingClient()
    loop = GraphAgenticLoop(
        client, max_iterations=2, augment_fn=_augment_lookup, enable_analytics=False
    )
    loop.run("what is acme's revenue")
    # No router -> no model kwarg added; ask sees the default None.
    assert all(m is None for m in client.models)
    assert len(client.models) >= 1


# --- optional reflection pass (seocho-t8m) ---------------------------------
from seocho.agent.reflection import Critique  # noqa: E402


def test_reflection_off_by_default_preserves_answer():
    client = _RecordingClient()
    loop = GraphAgenticLoop(client, max_iterations=1, augment_fn=_augment_lookup,
                            enable_analytics=False)
    r = loop.run("what is acme's revenue")
    assert r.reflected is False and r.reflection_revised is False
    assert r.final_answer  # an answer was produced, unchanged by reflection


def test_reflection_revises_final_answer_when_enabled():
    seen = {"n": 0}

    def critic(_task, _draft):
        seen["n"] += 1
        return Critique(ok=seen["n"] > 1, issues=["incomplete"] if seen["n"] == 1 else [])

    def reviser(_task, _draft, _crit):
        return "REVISED final answer with the missing piece added."

    loop = GraphAgenticLoop(_RecordingClient(), max_iterations=1, augment_fn=_augment_lookup,
                            enable_analytics=False, enable_reflection=True,
                            reflection_critic=critic, reflection_reviser=reviser)
    r = loop.run("what is acme's revenue")
    assert r.reflected is True and r.reflection_revised is True
    assert r.final_answer == "REVISED final answer with the missing piece added."


def test_reflection_error_never_degrades_answer():
    def boom(_task, _draft):
        raise RuntimeError("critic exploded")

    loop = GraphAgenticLoop(_RecordingClient(), max_iterations=1, augment_fn=_augment_lookup,
                            enable_analytics=False, enable_reflection=True,
                            reflection_critic=boom, reflection_reviser=lambda *a: "x")
    r = loop.run("what is acme's revenue")
    assert r.final_answer  # base answer preserved; exception swallowed
    assert r.reflection_revised is False
