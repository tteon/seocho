"""SDK surface for multi-LLM debate with convergence telemetry.

Closes seocho-vij5.

This module promotes the convergence-curve + early-stop logic from
``examples/teaching/chapter-05-debate-convergence-analysis.md`` (the Ch 5
appendix) to a stable SDK surface. The heavyweight orchestration (Society of
Mind / supervisor synthesis) already lives in ``extraction/debate.py``; this
module focuses on the *telemetry contract* learners and evaluators need:

- :class:`DebatePolicy` — declarative knobs (participants, max rounds,
  convergence threshold, time / cost budget, anti-pattern toggles).
- :func:`convergence_curve` — citation-Jaccard pairwise mean per round.
- :func:`should_stop` — the 5 early-stop criteria (convergence, no
  improvement, hard cap, time, cost) with named reason.
- :func:`select_participants` — intent → providers heuristic.
- :func:`detect_anti_patterns` — flags echo chamber / sycophancy /
  citation drift on a list of round panels.

The :class:`DebateResult` returned by orchestrators can be assembled from
these primitives; we deliberately do not wrap the orchestrator itself yet
because the runtime is still evolving — closing seocho-vij5 fully will
land in a follow-up that bridges :class:`extraction.debate.DebateOrchestrator`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


# ---------------------------------------------------------------------------
# Citation extraction (mirrors Ch 4/5 conventions)
# ---------------------------------------------------------------------------


_CITE_RE = re.compile(r"\[src:([^\]\s,]+)(?:,\s*chunk:(\d+))?\]")


def extract_citations(answer: str) -> Set[Tuple[str, str]]:
    """Pull ``[src:id]`` or ``[src:id, chunk:N]`` citations from an answer."""
    return {(src, chunk or "") for src, chunk in _CITE_RE.findall(answer or "")}


# ---------------------------------------------------------------------------
# Convergence
# ---------------------------------------------------------------------------


def convergence_curve(per_round_panels: Sequence[Mapping[str, str]]) -> List[float]:
    """Pairwise mean citation-Jaccard per round.

    ``per_round_panels[k][participant] == answer_text``.
    Returns a list ``[jacc_round_0, jacc_round_1, ...]``; 1.0 means full
    agreement on cited sources.
    """
    curve: List[float] = []
    for panel in per_round_panels:
        cite_sets = [extract_citations(a) for a in panel.values()]
        pairs: List[float] = []
        for i in range(len(cite_sets)):
            for j in range(i + 1, len(cite_sets)):
                a, b = cite_sets[i], cite_sets[j]
                pairs.append(len(a & b) / max(1, len(a | b)))
        curve.append(mean(pairs) if pairs else 0.0)
    return curve


def should_stop(
    curve: Sequence[float],
    *,
    elapsed_ms: int,
    tokens: int,
    max_rounds: int = 3,
    convergence_threshold: float = 0.80,
    no_improvement_eps: float = 0.05,
    time_budget_ms: int = 60_000,
    token_budget: int = 30_000,
) -> Tuple[bool, str]:
    """Return ``(stop, reason)``. ``reason`` is empty when not stopping."""
    if not curve:
        return False, ""
    last = curve[-1]
    if last >= convergence_threshold:
        return True, f"convergence reached ({last:.2f})"
    if len(curve) >= 3:
        d1 = abs(curve[-1] - curve[-2])
        d2 = abs(curve[-2] - curve[-3])
        if d1 < no_improvement_eps and d2 < no_improvement_eps:
            return True, "no improvement (2 stagnant rounds)"
    if len(curve) >= max_rounds:
        return True, f"hard round cap = {max_rounds}"
    if elapsed_ms >= time_budget_ms:
        return True, f"time budget {time_budget_ms}ms exceeded"
    if tokens >= token_budget:
        return True, f"token budget {token_budget} exceeded"
    return False, ""


# ---------------------------------------------------------------------------
# Participant selection
# ---------------------------------------------------------------------------


PARTICIPANT_PRESETS: Dict[str, List[str]] = {
    "lookup": ["openai", "deepseek"],
    "aggregation": ["openai", "deepseek"],
    "explanation": ["openai", "kimi"],
    "comparison": ["openai", "kimi", "deepseek", "grok"],
}


def select_participants(intent: str, *, available: Iterable[str]) -> List[str]:
    avail = list(available)
    desired = PARTICIPANT_PRESETS.get((intent or "").lower(), ["openai"])
    chosen = [p for p in desired if p in avail]
    return chosen or (avail[:1] if avail else [])


# ---------------------------------------------------------------------------
# Anti-patterns
# ---------------------------------------------------------------------------


def detect_anti_patterns(
    per_round_panels: Sequence[Mapping[str, str]],
    *,
    sycophancy_eps: float = 0.02,
) -> Dict[str, bool]:
    """Heuristic detection — outputs a bool per pattern.

    - ``echo_chamber``    — every round's pairwise Jaccard >= 0.95
    - ``sycophancy``      — Jaccard increases each round by <= ``sycophancy_eps``
                            yet never converges (monotone tiny improvements)
    - ``citation_drift``  — total cite-set size grows each round
    - ``context_drop``    — at least one round has 0 citations (heuristic)
    """
    curve = convergence_curve(per_round_panels)
    flags = {
        "echo_chamber": False,
        "sycophancy": False,
        "citation_drift": False,
        "context_drop": False,
    }
    if curve and all(j >= 0.95 for j in curve):
        flags["echo_chamber"] = True
    if len(curve) >= 3:
        diffs = [curve[i] - curve[i - 1] for i in range(1, len(curve))]
        if all(0 < d <= sycophancy_eps for d in diffs):
            flags["sycophancy"] = True
    cite_sizes = []
    for panel in per_round_panels:
        union: Set[Tuple[str, str]] = set()
        for a in panel.values():
            union |= extract_citations(a)
        cite_sizes.append(len(union))
    if len(cite_sizes) >= 2 and all(
        cite_sizes[i] > cite_sizes[i - 1] for i in range(1, len(cite_sizes))
    ):
        flags["citation_drift"] = True
    if any(size == 0 for size in cite_sizes):
        flags["context_drop"] = True
    return flags


# ---------------------------------------------------------------------------
# Policy + Result
# ---------------------------------------------------------------------------


@dataclass
class DebatePolicy:
    """Declarative knobs for a debate run."""

    participants: List[str] = field(default_factory=lambda: ["openai", "kimi"])
    moderator_chain: List[str] = field(
        default_factory=lambda: ["openai", "kimi", "deepseek"]
    )
    max_rounds: int = 3
    convergence_threshold: float = 0.80
    no_improvement_eps: float = 0.05
    time_budget_ms: int = 60_000
    token_budget: int = 30_000

    @classmethod
    def for_intent(cls, intent: str, *, available: Iterable[str]) -> "DebatePolicy":
        chosen = select_participants(intent, available=available)
        return cls(participants=chosen or ["openai"])


@dataclass
class DebateResult:
    """Telemetry-friendly result. Orchestrators populate this."""

    final_answer: str
    curve: List[float] = field(default_factory=list)
    stop_reason: str = ""
    rounds_run: int = 0
    tokens_used: int = 0
    latency_ms: int = 0
    anti_patterns: Dict[str, bool] = field(default_factory=dict)
    panel_history: List[Dict[str, str]] = field(default_factory=list)

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "debate.curve": self.curve,
            "debate.stop_reason": self.stop_reason,
            "debate.rounds_run": self.rounds_run,
            "debate.tokens": self.tokens_used,
            "debate.latency_ms": self.latency_ms,
            **{f"debate.anti.{k}": v for k, v in self.anti_patterns.items()},
        }


__all__ = [
    "DebatePolicy",
    "DebateResult",
    "convergence_curve",
    "detect_anti_patterns",
    "extract_citations",
    "select_participants",
    "should_stop",
    "PARTICIPANT_PRESETS",
]
