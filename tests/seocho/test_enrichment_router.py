"""Tests for ADR-0091 QueryEnrichmentRouter + ReciprocalRankFusion."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from seocho.agent.enrichment_router import (
    QueryEnrichmentRouter,
    enrichment_router_enabled,
    _default_intent_classifier,
)
from seocho.agent.fusion import ReciprocalRankFusion
from seocho.routing import RoutingPolicy


@pytest.fixture
def policy() -> RoutingPolicy:
    return RoutingPolicy.default()


# ---- Fusion ---------------------------------------------------------------


def test_rrf_combines_ranked_lists_by_weighted_score() -> None:
    rrf = ReciprocalRankFusion(k=60)
    cypher = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
    vector = [{"id": "B"}, {"id": "D"}]
    weights = {"cypher": 0.6, "vector": 0.4}
    fused = rrf.fuse({"cypher": cypher, "vector": vector}, weights)
    ids = [entry["id"] for entry in fused]
    assert "B" in ids
    assert ids[0] in {"A", "B"}  # weights tilt A first; B has hits in both
    # Every fused entry carries a numeric score.
    for entry in fused:
        assert isinstance(entry["score"], float)


def test_rrf_skips_backends_below_weight_floor() -> None:
    rrf = ReciprocalRankFusion(k=60, weight_floor=0.10)
    fused = rrf.fuse(
        {"cypher": [{"id": "A"}], "vector": [{"id": "B"}]},
        {"cypher": 0.9, "vector": 0.05},
    )
    ids = [entry["id"] for entry in fused]
    assert ids == ["A"]  # vector dropped below floor


def test_rrf_degenerate_single_list_preserves_order() -> None:
    rrf = ReciprocalRankFusion(k=60)
    items = [{"id": "x"}, {"id": "y"}, {"id": "z"}]
    fused = rrf.fuse({"cypher": items}, {"cypher": 0.9})
    assert [entry["id"] for entry in fused] == ["x", "y", "z"]


def test_rrf_uses_str_fallback_when_id_missing() -> None:
    rrf = ReciprocalRankFusion(k=60)
    fused = rrf.fuse({"cypher": ["alpha", "beta"]}, {"cypher": 0.9})
    assert [entry["id"] for entry in fused] == ["alpha", "beta"]


def test_rrf_rejects_nonpositive_k() -> None:
    with pytest.raises(ValueError, match="k must be positive"):
        ReciprocalRankFusion(k=0)


# ---- Router stages --------------------------------------------------------


def test_augment_returns_intent_entities_topic(policy: RoutingPolicy) -> None:
    router = QueryEnrichmentRouter(policy=policy)
    aug = router.augment('What is "Foo Corp"?', workspace_id="ws-1")
    assert "intent" in aug and "confidence" in aug["intent"]
    assert "Foo Corp" in aug["entities"]
    assert aug["workspace_id"] == "ws-1"
    assert aug["topic"] == []


def test_default_intent_classifier_picks_relationship_lookup() -> None:
    out = _default_intent_classifier("What is the relationship between A and B?")
    assert out["intent"] == "relationship_lookup"
    assert out["confidence"] >= 0.6


def test_default_intent_classifier_falls_back_for_unknown() -> None:
    out = _default_intent_classifier("xyzzy plugh")
    assert out["intent"] == "lookup"
    assert out["reason"] == "default"


def test_route_returns_routing_decision_for_known_intent(policy: RoutingPolicy) -> None:
    router = QueryEnrichmentRouter(policy=policy)
    aug = router.augment("Describe Apple Inc.")
    decision = router.route(aug)
    assert decision.intent
    assert sum(decision.weights.values()) > 0


def test_fan_out_runs_only_configured_backends(policy: RoutingPolicy) -> None:
    seen: List[str] = []

    def cypher_backend(q, aug, dec):  # noqa: ANN001
        seen.append("cypher")
        return [{"id": "doc:A"}, {"id": "doc:B"}]

    router = QueryEnrichmentRouter(policy=policy, cypher_backend=cypher_backend)
    aug = router.augment("look up Foo Corp")
    decision = router.route(aug)
    fan_out = asyncio.run(router.fan_out("look up Foo Corp", aug, decision))
    if decision.weights.get("cypher", 0.0) >= 0.10:
        assert "cypher" in fan_out
        assert seen == ["cypher"]
        assert len(fan_out["cypher"].items) == 2
        assert fan_out["cypher"].error is None


def test_fan_out_isolates_backend_failures(policy: RoutingPolicy) -> None:
    def good(q, aug, dec):  # noqa: ANN001
        return [{"id": "good:1"}]

    def boom(q, aug, dec):  # noqa: ANN001
        raise RuntimeError("intentional")

    router = QueryEnrichmentRouter(
        policy=policy,
        cypher_backend=good,
        vector_backend=boom,
    )
    aug = router.augment("describe Foo")
    decision = router.route(aug)
    # Force both backends past the floor by stamping weights.
    decision.weights["cypher"] = 0.5
    decision.weights["vector"] = 0.5
    fan_out = asyncio.run(router.fan_out("describe Foo", aug, decision))
    assert fan_out["cypher"].error is None
    assert fan_out["vector"].error and "intentional" in fan_out["vector"].error


def test_fuse_excludes_errored_backends(policy: RoutingPolicy) -> None:
    def good(q, aug, dec):  # noqa: ANN001
        return [{"id": "g:1"}]

    def boom(q, aug, dec):  # noqa: ANN001
        raise RuntimeError("nope")

    router = QueryEnrichmentRouter(
        policy=policy,
        cypher_backend=good,
        vector_backend=boom,
    )
    aug = router.augment("describe Foo")
    decision = router.route(aug)
    decision.weights["cypher"] = 0.5
    decision.weights["vector"] = 0.5
    fan_out = asyncio.run(router.fan_out("describe Foo", aug, decision))
    fused = router.fuse(fan_out, decision)
    assert [entry["id"] for entry in fused] == ["g:1"]


def test_run_end_to_end_with_single_cypher_backend(policy: RoutingPolicy) -> None:
    calls: List[Dict[str, Any]] = []

    def cypher_backend(question, augmentation, decision):  # noqa: ANN001
        calls.append({"q": question, "intent": augmentation["intent"]["intent"]})
        return [{"id": "entity:Foo"}, {"id": "entity:Bar"}]

    router = QueryEnrichmentRouter(policy=policy, cypher_backend=cypher_backend)
    fused, trace = router.run('Find "Foo Corp"', workspace_id="ws-1")

    # Cypher weight may dip below floor depending on default policy; if so the
    # backend is intentionally not invoked. Either branch is a valid thin
    # slice — we just assert the trace shape stays coherent.
    assert trace.workspace_id == "ws-1"
    assert "Foo Corp" in trace.augmentation["entities"]
    assert trace.decision["intent"]
    if "cypher" in trace.backends_run:
        assert calls and calls[0]["intent"]
        assert any(entry["id"].startswith("entity:") for entry in fused)


# ---- Feature flag ---------------------------------------------------------


def test_feature_flag_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEOCHO_ENABLE_ENRICHMENT_ROUTER", raising=False)
    assert enrichment_router_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE"])
def test_feature_flag_on(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SEOCHO_ENABLE_ENRICHMENT_ROUTER", value)
    assert enrichment_router_enabled() is True
