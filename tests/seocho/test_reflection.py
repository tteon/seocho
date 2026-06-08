"""Tests for the reflection (self-critique → revise) pattern.

Deterministic layers use stub critic/reviser; the live layer (MARA, skipped
without a key) proves the benefit empirically: a factually wrong draft is
caught and corrected by the loop.
"""

from __future__ import annotations

import os
import re

import pytest

from seocho.agent.reflection import (
    Critique,
    ReflectionResult,
    make_llm_critic,
    make_llm_reviser,
    reflect,
)


# --------------------------------------------------------------------------- #
# Deterministic loop behavior
# --------------------------------------------------------------------------- #

def test_revises_until_critic_satisfied():
    # critic flags once, then is satisfied after the reviser fixes it.
    calls = {"n": 0}

    def critic(task, draft):
        calls["n"] += 1
        if "[fixed]" in draft:
            return Critique(ok=True)
        return Critique(ok=False, issues=["missing fix"])

    def reviser(task, draft, critique):
        return draft + " [fixed]"

    res = reflect("t", "raw answer", critic=critic, reviser=reviser, max_iterations=3)
    assert res.final == "raw answer [fixed]"
    assert res.revised is True
    assert res.iterations == 2          # flag round + satisfied round
    assert res.history[-1][1].ok is True


def test_no_revision_when_first_draft_is_ok():
    def critic(task, draft):
        return Critique(ok=True)

    def reviser(task, draft, critique):  # pragma: no cover - must not be called
        raise AssertionError("reviser should not run when draft is already ok")

    res = reflect("t", "good", critic=critic, reviser=reviser)
    assert res.final == "good" and res.revised is False and res.iterations == 1


def test_stops_at_max_iterations_even_if_never_satisfied():
    def critic(task, draft):
        return Critique(ok=False, issues=["still bad"])

    def reviser(task, draft, critique):
        return draft + "x"

    res = reflect("t", "a", critic=critic, reviser=reviser, max_iterations=2)
    assert res.iterations == 2
    assert res.final == "axx"  # revised twice, never satisfied
    assert res.revised is True


def test_max_iterations_must_be_positive():
    with pytest.raises(ValueError):
        reflect("t", "d", critic=lambda *_: Critique(ok=True),
                reviser=lambda *_: "", max_iterations=0)


# --------------------------------------------------------------------------- #
# Live MARA — reflection corrects a factual error
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
def test_reflection_fixes_wrong_answer_live():
    key = _mara_key()
    if not key:
        pytest.skip("MARA_API_KEY not available")
    pytest.importorskip("openai")
    from openai import OpenAI

    client = OpenAI(api_key=key, base_url="https://api.cloud.mara.com/v1")
    critic = make_llm_critic(client, "DeepSeek-V3.1")
    reviser = make_llm_reviser(client, "DeepSeek-V3.1")

    task = "State the capital of France in one word."
    wrong_draft = "The capital of France is Berlin."

    res = reflect(task, wrong_draft, critic=critic, reviser=reviser, max_iterations=2)
    # The benefit: self-critique caught the error and the revision corrected it.
    assert res.revised is True, res.history
    assert "paris" in res.final.lower(), f"reflection did not correct the answer: {res.final!r}"
    assert "berlin" not in res.final.lower()
