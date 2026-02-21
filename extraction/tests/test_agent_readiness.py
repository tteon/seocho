"""Tests for agent readiness state summarization."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_readiness import summarize_readiness


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
