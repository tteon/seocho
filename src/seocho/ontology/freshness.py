"""Bounded-staleness freshness policy for the ontology-drift barrier (seocho-ia4.6).

ia4.1 (ADR-0175) turned drift into a *binary* barrier: any version mismatch →
block. But unconditional strictness is itself a correctness failure in the other
direction — it **over-refuses**. Most drift is benign: the changed part of the
ontology doesn't touch the subgraph a given query reads, or the data is only one
*compatible* version behind and can be reconciled on read. Conversely "warn"
(serve everything) **under-refuses**: it answers against data the drift has
actually invalidated. Neither fixed policy is right.

The correct posture is **bounded staleness**: refuse only when the drift is BOTH
relevant to the query AND beyond a staleness bound; otherwise serve (or
repair-on-read). Staleness is a *distance*, not a boolean:

- ``version_distance`` — how many versions the data lags the active contract.
- ``drift_relevance`` — does the change touch the labels this query reads?
- ``stamp_coverage`` — can we even tell (from the drift-coverage probe)?
- ``version_age_days`` — is the certified contract itself stale?

This is the systems argument the review made concrete: the ontology is
simultaneously the type system AND data, so a version bump changes the meaning of
validation. "Strict" is only safe when recency and relevance are judged — this
module is that judgment. See wiki/ontology-lifecycle-os-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FreshnessSignals:
    """Signals for a freshness decision (all derivable from existing artifacts)."""

    version_mismatch: bool          # active context_hash != any indexed hash
    version_distance: int = 1       # versions/epochs the data lags the active contract
    drift_relevance: float = 1.0    # in [0,1]: fraction of the query's labels the change touches
    stamp_coverage: float = 1.0     # stamped / scoped nodes (from the coverage probe)
    version_age_days: Optional[float] = None


@dataclass(frozen=True)
class FreshnessDecision:
    decision: str                   # "serve" | "repair" | "refuse"
    reason: str
    staleness: float                # version_distance * drift_relevance

    @property
    def blocks(self) -> bool:
        return self.decision == "refuse"


def evaluate_freshness(
    sig: FreshnessSignals,
    *,
    max_version_distance: int = 1,
    min_relevance: float = 1e-9,
    min_coverage: float = 0.5,
    max_age_days: Optional[float] = None,
) -> FreshnessDecision:
    """Decide serve / repair / refuse for a drifted read under a staleness bound.

    - fresh (no version mismatch) → **serve**.
    - can't verify (stamp coverage below ``min_coverage``) → **refuse** (blind).
    - drift irrelevant to the query (``drift_relevance <= min_relevance``) →
      **serve** (the change doesn't touch what this query reads).
    - certified contract older than ``max_age_days`` → **refuse** (contract stale).
    - relevant drift within ``max_version_distance`` → **repair** (reconcilable
      on read; served).
    - relevant drift beyond the bound → **refuse** (not reconcilable).
    """
    staleness = float(max(sig.version_distance, 0)) * max(float(sig.drift_relevance), 0.0)
    if not sig.version_mismatch:
        return FreshnessDecision("serve", "fresh", 0.0)
    if sig.stamp_coverage < min_coverage:
        return FreshnessDecision("refuse", "insufficient_stamp_coverage", staleness)
    if sig.drift_relevance <= min_relevance:
        return FreshnessDecision("serve", "drift_irrelevant_to_query", staleness)
    if max_age_days is not None and (sig.version_age_days or 0.0) > max_age_days:
        return FreshnessDecision("refuse", "contract_too_old", staleness)
    if sig.version_distance <= max_version_distance:
        return FreshnessDecision("repair", "within_staleness_bound", staleness)
    return FreshnessDecision("refuse", "beyond_staleness_bound", staleness)


def freshness_to_drift_policy(decision: FreshnessDecision) -> str:
    """Bridge a freshness decision to the ia4.1 barrier's policy vocabulary:
    ``refuse`` → 'block'; ``serve``/``repair`` → 'warn' (proceed)."""
    return "block" if decision.blocks else "warn"
