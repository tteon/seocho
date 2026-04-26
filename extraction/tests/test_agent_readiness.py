"""Tests for agent readiness state summarization."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from runtime.agent_readiness import summarize_readiness


def test_summarize_readiness_ready():
    summary = summarize_readiness(
        [
            {"database": "kgnormal", "status": "ready"},
            {"database": "kgfibo", "status": "ready"},
        ]
    )
    assert summary["debate_state"] == "ready"
    assert summary["degraded"] is False


def test_summarize_readiness_degraded():
    summary = summarize_readiness(
        [
            {"database": "kgnormal", "status": "ready"},
            {"database": "kgfibo", "status": "degraded"},
        ]
    )
    assert summary["debate_state"] == "degraded"
    assert summary["degraded"] is True


def test_summarize_readiness_blocked():
    summary = summarize_readiness(
        [
            {"database": "kgnormal", "status": "degraded"},
            {"database": "kgfibo", "status": "degraded"},
        ]
    )
    assert summary["debate_state"] == "blocked"
    assert summary["ready_count"] == 0


def test_summarize_readiness_initializing_degrades_when_some_graphs_are_ready():
    summary = summarize_readiness(
        [
            {"database": "kgnormal", "status": "ready"},
            {"database": "kgfibo", "status": "initializing"},
        ]
    )
    assert summary["debate_state"] == "degraded"
    assert summary["degraded"] is True
    assert summary["degraded_count"] == 1


# ---------------------------------------------------------------------------
# Phase 3 — composed readiness guard (state x hash)
# ---------------------------------------------------------------------------


def test_summarize_readiness_surfaces_ontology_hash_skew():
    """Status entries from Phase 2 carry ontology_context_mismatch when drift
    is detected; the rollup must expose mismatch_count + mismatch_graph_ids
    so the router treats skewed agents the same as DEGRADED ones."""

    summary = summarize_readiness(
        [
            {"graph": "kgnormal", "database": "kgnormal", "status": "ready"},
            {
                "graph": "kgfibo",
                "database": "kgfibo",
                "status": "degraded",
                "reason": "ontology_context_mismatch",
                "ontology_context_mismatch": {
                    "active_context_hash": "hashNew",
                    "indexed_context_hashes": ["hashOld"],
                },
            },
        ]
    )
    assert summary["mismatch_count"] == 1
    assert summary["mismatch_graph_ids"] == ["kgfibo"]
    assert summary["debate_state"] == "degraded"
    assert summary["degraded"] is True


def test_summarize_readiness_no_skew_reports_empty_mismatch_fields():
    summary = summarize_readiness(
        [
            {"graph": "kgnormal", "database": "kgnormal", "status": "ready"},
        ]
    )
    assert summary["mismatch_count"] == 0
    assert summary["mismatch_graph_ids"] == []


def test_summarize_readiness_skew_without_status_field_still_counted():
    """Defensive: even if a caller forgets to set status='degraded', the
    presence of ontology_context_mismatch alone is enough to surface in
    the rollup."""

    summary = summarize_readiness(
        [
            {"graph": "kgnormal", "database": "kgnormal", "status": "ready"},
            {
                "graph": "kgfibo",
                "database": "kgfibo",
                "status": "ready",
                "ontology_context_mismatch": {"active_context_hash": "x"},
            },
        ]
    )
    assert summary["mismatch_count"] == 1
    assert "kgfibo" in summary["mismatch_graph_ids"]


# State machine composition tests --------------------------------------------


def _state_machine():
    from runtime.agent_state import AgentStateMachine

    return AgentStateMachine()


def test_can_query_graph_requires_ready_state():
    sm = _state_machine()
    assert sm.can_query_graph() is False  # INITIALIZING
    sm.mark_degraded("warming up")
    assert sm.can_query_graph() is False
    sm.mark_ready()
    assert sm.can_query_graph() is True


def test_can_query_graph_blocks_when_skew_attached_even_if_ready():
    """Phase 3's structural property: degraded-on-skew is enforced in one place."""

    sm = _state_machine()
    sm.mark_ready()
    assert sm.can_query_graph() is True

    sm.set_ontology_context_skew(
        {
            "active_context_hash": "hashNew",
            "indexed_context_hashes": ["hashOld"],
            "graph_id": "kgfibo",
            "database": "kgfibo",
            "workspace_id": "default",
        }
    )
    assert sm.can_query_graph() is False
    # can_answer still allows synthesis from peer graphs
    assert sm.can_answer() is True

    sm.set_ontology_context_skew(None)
    assert sm.can_query_graph() is True


def test_can_answer_independent_of_skew():
    """can_answer composes only state, not hash — debate orchestration can still
    synthesize from unaffected peers when one graph has drifted."""

    sm = _state_machine()
    sm.mark_ready()
    sm.set_ontology_context_skew({"any": "evidence"})
    assert sm.can_answer() is True
    assert sm.can_query_graph() is False
