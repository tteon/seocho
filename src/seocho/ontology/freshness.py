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

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple


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
    ``refuse`` → 'block'; ``serve``/``repair`` → 'warn' (proceed).

    Note that ``repair`` is no longer *only* 'warn-and-serve-as-is': the caller
    that proceeds on a ``repair`` decision should reconcile the read with
    :func:`repair_read` (below) before answering — 'warn' governs the barrier,
    ``repair_read`` performs the reconciliation. See :func:`apply_read_repair`."""
    return "block" if decision.blocks else "warn"


# ---------------------------------------------------------------------------
# Read-time repair — the reconciliation that makes a "repair" decision real.
#
# The review flagged "repair" as stubbed to serve. A drifted-but-within-bound
# read is served against data indexed under an older contract; to conform to the
# ACTIVE contract we reconcile the retrieved records on the way out:
#
#   - drop records that reference a **soft-deleted** node — one the migration
#     logically removed (``_ontology_soft_deleted_at`` stamp, ia4.5). Serving
#     logically-removed data is exactly the staleness the barrier exists to stop.
#   - strip **deprecated properties** the active schema no longer declares.
#
# Both are O(records) scans of self-describing data: NO ontology reasoning on the
# hot path (the ``keep ontology reasoning out of hot request paths`` guardrail).
# The deprecated-property set is derived once off the hot path (:func:`plan_read_repair`).
# ---------------------------------------------------------------------------

_SOFT_DELETE_KEY = "_ontology_soft_deleted_at"


@dataclass(frozen=True)
class ReadRepairReport:
    dropped_records: int = 0          # records referencing a soft-deleted node
    stripped_property_keys: int = 0   # deprecated property values removed
    reconcilable: bool = True         # False if a breaking change blocks read-repair

    def to_dict(self) -> Dict[str, Any]:
        return {"dropped_records": self.dropped_records,
                "stripped_property_keys": self.stripped_property_keys,
                "reconcilable": self.reconcilable}


@dataclass(frozen=True)
class ReadRepairPlan:
    """The reconcilable-on-read part of a schema change, precomputed off the hot
    path from a migration plan."""
    deprecated_properties: FrozenSet[str] = field(default_factory=frozenset)
    removed_labels: FrozenSet[str] = field(default_factory=frozenset)
    reconcilable: bool = True         # a breaking change (e.g. prop retyped) is NOT read-repairable
    reason: str = ""


def plan_read_repair(migration_plan: Dict[str, Any]) -> ReadRepairPlan:
    """Derive the read-repair plan from ``Ontology.migration_plan`` output.

    Removed properties/labels are read-repairable (strip / filter on read); a
    change that alters the *meaning* of retained data (a retype, a newly-required
    property) is NOT — such a read must refuse, not silently reconcile."""
    removals = migration_plan.get("removals", []) or []
    deprecated = frozenset(r.get("property") for r in removals
                           if r.get("type") == "property" and r.get("property"))
    removed_labels = frozenset(r.get("label") for r in removals
                               if r.get("type") == "node" and r.get("label"))
    # A retype / required-add is a semantic break we cannot fix by dropping data.
    breaking_unrepairable = any(
        s.get("data_loss") for s in migration_plan.get("cypher_statements", []) or []
    )
    reconcilable = not breaking_unrepairable
    reason = "" if reconcilable else "breaking_change_not_read_repairable"
    return ReadRepairPlan(deprecated, removed_labels, reconcilable, reason)


def _is_soft_deleted(value: Any) -> bool:
    if isinstance(value, dict):
        if any(k == _SOFT_DELETE_KEY or str(k).endswith("." + _SOFT_DELETE_KEY) for k in value):
            return True
        return any(_is_soft_deleted(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_is_soft_deleted(v) for v in value)
    return False


def _strip_props(value: Any, deprecated: FrozenSet[str], counter: List[int]) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in deprecated or any(str(k).endswith("." + d) for d in deprecated):
                counter[0] += 1
                continue
            out[k] = _strip_props(v, deprecated, counter)
        return out
    if isinstance(value, list):
        return [_strip_props(v, deprecated, counter) for v in value]
    return value


def repair_read(
    records: Optional[List[Any]],
    *,
    deprecated_properties: FrozenSet[str] = frozenset(),
    reconcilable: bool = True,
) -> Tuple[List[Any], ReadRepairReport]:
    """Reconcile retrieved ``records`` to the active contract.

    Drops any record referencing a soft-deleted node and strips deprecated
    property values. Cheap (O(records), self-describing data). If
    ``reconcilable`` is False (a breaking change), returns the records unchanged
    with ``reconcilable=False`` in the report so the caller escalates to refuse."""
    recs = list(records or [])
    if not reconcilable:
        return recs, ReadRepairReport(reconcilable=False)
    kept: List[Any] = []
    dropped = 0
    counter = [0]
    for rec in recs:
        if _is_soft_deleted(rec):
            dropped += 1
            continue
        kept.append(_strip_props(rec, deprecated_properties, counter)
                    if deprecated_properties else rec)
    return kept, ReadRepairReport(dropped_records=dropped,
                                  stripped_property_keys=counter[0],
                                  reconcilable=True)
