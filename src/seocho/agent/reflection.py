"""Reflection pattern — have an agent critique and revise its own output.

SEOCHO already has *post-hoc patching* (graph_cot_flow's supervisor.revise,
insufficiency assessment, graph_loop._evaluate), but no reusable, agent-driven
*self-critique → revise* loop. This module adds that pattern as a small,
provider-agnostic component:

    draft → critic(task, draft) → if issues: reviser(task, draft, critique) → repeat

The critic and reviser are injected (``CriticFn`` / ``ReviserFn``) so the loop
is deterministic and unit-testable with stubs, and LLM-backed in production via
:func:`make_llm_critic` / :func:`make_llm_reviser` (any OpenAI-compatible client,
including MARA). The loop stops as soon as the critic reports no issues, or after
``max_iterations`` — quality over speed, bounded so it can't run away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

__all__ = [
    "Critique",
    "ReflectionResult",
    "CriticFn",
    "ReviserFn",
    "reflect",
    "make_llm_critic",
    "make_llm_reviser",
]


@dataclass(frozen=True)
class Critique:
    """A critic's verdict on a draft. ``ok=True`` means stop (good enough)."""

    ok: bool
    issues: List[str] = field(default_factory=list)
    raw: str = ""


@dataclass
class ReflectionResult:
    final: str
    iterations: int                       # number of critique rounds performed
    revised: bool                         # whether the draft was changed
    history: List[Tuple[str, Critique]]   # (draft_seen, critique) per round


# (task, draft) -> Critique ; (task, draft, critique) -> revised draft
CriticFn = Callable[[str, str], Critique]
ReviserFn = Callable[[str, str, Critique], str]


def reflect(
    task: str,
    draft: str,
    *,
    critic: CriticFn,
    reviser: ReviserFn,
    max_iterations: int = 2,
) -> ReflectionResult:
    """Run the critique→revise loop until the critic is satisfied or the budget runs out."""
    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")
    current = draft
    history: List[Tuple[str, Critique]] = []
    revised = False
    for _ in range(max_iterations):
        critique = critic(task, current)
        history.append((current, critique))
        if critique.ok:
            break
        improved = reviser(task, current, critique)
        if improved != current:
            revised = True
        current = improved
    return ReflectionResult(
        final=current, iterations=len(history), revised=revised, history=history
    )


# --------------------------------------------------------------------------- #
# LLM-backed critic / reviser (OpenAI-compatible; works with MARA)
# --------------------------------------------------------------------------- #

_CRITIC_SYSTEM = (
    "You are a meticulous reviewer. Given a TASK and a DRAFT answer, judge whether "
    "the draft is correct and complete for the task. If it is fully correct, reply "
    "with exactly 'OK'. Otherwise reply with a short list of the concrete problems."
)
_REVISER_SYSTEM = (
    "You revise answers. Given a TASK, a DRAFT, and a CRITIQUE listing problems, "
    "produce a corrected, improved answer for the task. Output only the revised answer."
)


def _chat(client, model: str, system: str, user: str, *, temperature: float = 0.0) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    return (resp.choices[0].message.content or "").strip()


def make_llm_critic(client, model: str, *, ok_token: str = "OK") -> CriticFn:
    """Build a critic from an OpenAI-compatible ``client``.

    The critic asks the model to reply ``OK`` when the draft is correct, else to
    list problems. ``ok`` is True when the reply is exactly the ok token (case-
    insensitive) and lists no problems.
    """

    def _critic(task: str, draft: str) -> Critique:
        out = _chat(client, model, _CRITIC_SYSTEM, f"TASK:\n{task}\n\nDRAFT:\n{draft}")
        stripped = out.strip()
        ok = stripped.upper() == ok_token.upper()
        issues = [] if ok else [line for line in stripped.splitlines() if line.strip()]
        return Critique(ok=ok, issues=issues, raw=out)

    return _critic


def make_llm_reviser(client, model: str) -> ReviserFn:
    """Build a reviser from an OpenAI-compatible ``client``."""

    def _reviser(task: str, draft: str, critique: Critique) -> str:
        user = (
            f"TASK:\n{task}\n\nDRAFT:\n{draft}\n\n"
            f"CRITIQUE (problems to fix):\n" + "\n".join(critique.issues or [critique.raw])
        )
        return _chat(client, model, _REVISER_SYSTEM, user)

    return _reviser
