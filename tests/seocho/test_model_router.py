"""Tests for the cost-aware model-tier router (seocho routing).

Three layers:
1. Unit — the signal→tier→model decision is correct and deterministic.
2. Regression — on a realistic request mix the router demonstrably cuts cost
   vs sending everything to the frontier model (the benefit, quantified).
3. Live (MARA, skipped without a key) — the FAST tier actually answers a
   routine request correctly, proving cheap models are good enough for it.
"""

from __future__ import annotations

import os
import re

import pytest

from seocho.routing import ModelRouter, ModelTier, estimate_workload_cost


@pytest.fixture
def router() -> ModelRouter:
    return ModelRouter.mara_default()


# --------------------------------------------------------------------------- #
# 1. Unit — decision correctness
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("intent,tier", [
    ("lookup", ModelTier.FAST),
    ("aggregation", ModelTier.BALANCED),
    ("comparison", ModelTier.FRONTIER),
    ("explanation", ModelTier.FRONTIER),
])
def test_route_on_intent(router, intent, tier):
    assert router.route(intent=intent).tier == tier


@pytest.mark.parametrize("mode,tier", [
    ("semantic", ModelTier.BALANCED),
    ("graph_cot", ModelTier.FRONTIER),
])
def test_route_on_query_mode(router, mode, tier):
    assert router.route(query_mode=mode).tier == tier


@pytest.mark.parametrize("task,tier", [
    ("commit_message", ModelTier.FAST),
    ("summarize", ModelTier.FAST),
    ("extract", ModelTier.BALANCED),
    ("plan", ModelTier.FRONTIER),
    ("debate", ModelTier.FRONTIER),
])
def test_route_on_task(router, task, tier):
    assert router.route(task=task).tier == tier


def test_most_specific_signal_wins():
    # task ("commit_message"=FAST) overrides a frontier-leaning intent.
    r = ModelRouter.mara_default()
    res = r.route(intent="explanation", query_mode="graph_cot", task="commit_message")
    assert res.tier == ModelTier.FAST
    assert res.signal.startswith("task=")


def test_default_tier_when_no_known_signal(router):
    assert router.route().tier == ModelTier.BALANCED
    assert router.route(intent="unknown_intent").tier == ModelTier.BALANCED


def test_signal_resolves_to_concrete_model(router):
    res = router.route(intent="lookup")
    assert res.model == "DeepSeek-V3.1" and res.tier == ModelTier.FAST


def test_escalation_bumps_tier(router):
    assert router.route(intent="lookup").tier == ModelTier.FAST
    assert router.route(intent="lookup", escalate=1).tier == ModelTier.BALANCED
    assert router.route(intent="lookup", escalate=2).tier == ModelTier.FRONTIER
    # capped at FRONTIER
    assert router.route(intent="comparison", escalate=5).tier == ModelTier.FRONTIER


def test_nearest_tier_fallback_for_partial_map():
    # No BALANCED model configured -> a BALANCED request resolves to the nearest.
    r = ModelRouter(
        tier_models={ModelTier.FAST: "cheap", ModelTier.FRONTIER: "big"},
        default_tier=ModelTier.FAST,
    )
    res = r.route(intent="aggregation")  # wants BALANCED
    assert res.model in {"cheap", "big"}  # nearest available, never crashes


def test_empty_tier_map_rejected():
    with pytest.raises(ValueError):
        ModelRouter(tier_models={})


# --------------------------------------------------------------------------- #
# 2. Regression — quantified cost benefit on a realistic mix
# --------------------------------------------------------------------------- #

def test_routing_cuts_cost_on_realistic_mix(router):
    # Kilo/Anyscale finding: 80-90% of agent requests don't need a frontier
    # model. Model that mix: mostly cheap signals, a few hard ones.
    workload = (
        [{"task": "commit_message"}] * 30
        + [{"intent": "lookup"}] * 40
        + [{"intent": "aggregation"}] * 15
        + [{"query_mode": "semantic"}] * 5
        + [{"intent": "comparison"}] * 5
        + [{"query_mode": "graph_cot"}] * 5
    )
    stats = estimate_workload_cost(router, workload)
    # Sending everything to frontier would cost 10x/request; routing should save
    # well over half (the field's reported 40-70% range; this mix lands higher).
    assert stats["saving_ratio"] > 0.6, stats
    assert stats["routed_cost"] < stats["all_frontier_cost"]


def test_all_frontier_workload_saves_nothing(router):
    # Sanity: if every request genuinely needs the frontier, there's no saving
    # to fake — the estimate must report ~0, not a phantom win.
    workload = [{"query_mode": "graph_cot"}] * 10
    stats = estimate_workload_cost(router, workload)
    assert stats["saving_ratio"] == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# 3. Live MARA — the FAST tier is good enough for routine work
# --------------------------------------------------------------------------- #

def _mara_key() -> str | None:
    key = os.getenv("MARA_API_KEY")
    if key:
        return key
    try:
        for line in open(os.path.join(os.getcwd(), ".env"), encoding="utf-8"):
            m = re.match(r'\s*MARA_API_KEY\s*=\s*"?([^"\n]+)"?', line)
            if m:
                return m.group(1).strip()
    except OSError:
        pass
    return None


@pytest.mark.integration
def test_fast_tier_handles_routine_request_live():
    key = _mara_key()
    if not key:
        pytest.skip("MARA_API_KEY not available")
    pytest.importorskip("openai")
    from openai import OpenAI

    router = ModelRouter.mara_default()
    # A routine lookup -> FAST tier. Prove the cheap model actually answers it.
    route = router.route(intent="lookup")
    assert route.tier == ModelTier.FAST

    client = OpenAI(api_key=key, base_url="https://api.cloud.mara.com/v1")
    resp = client.chat.completions.create(
        model=route.model,
        messages=[
            {"role": "system", "content": "Answer with the single word only."},
            {"role": "user", "content": "What is the capital of France?"},
        ],
        temperature=0.0,
        max_tokens=10,
    )
    answer = (resp.choices[0].message.content or "").strip().lower()
    assert "paris" in answer, f"FAST tier ({route.model}) failed routine request: {answer!r}"
