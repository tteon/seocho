"""GOPTS G4 Layer-1 — cost-model ranking-quality harness (ADR-0097).

Validates the cost model from G2 against a ground-truth ranking.
Per the user-confirmed scope (this sprint), only Layer 1 ships:
"when the cost model ranks K candidate plans, does it agree with
reality?" Reality is whatever the ``oracle_fn`` callable returns —
unit tests pass a mock, live runs wire it through Neo4j's PROFILE
output to get db_hits-based ground truth.

Metrics:
- ``top1_accuracy`` — does the cost model pick the PROFILE-optimal plan?
- ``ndcg_at_k`` — does the cost ranking match the oracle ranking near
  the top? Graded relevance fallback for cases where multiple plans
  are near-optimal.
- ``kendall_tau`` — full-ranking agreement, independent of top-1.

The harness depends on G1 (IndexStats payload), G2 (cost_model +
PatternSpec.alternatives), and G3 (pattern_catalog.enumerate_for_shape).
Layer 2 (end-to-end latency) and Layer 3 (GraphRAG-style answer
quality) are explicit follow-ups; the data shape recorded by the
harness leaves room for both (see NLCypherExample G4 fields in
seocho/store/vector.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..query import cost_model, pattern_catalog
from ..query.contracts import PatternSpec


# ---------------------------------------------------------------------------
# Fixture model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoptsFixture:
    """One NL→pattern fixture case for Layer-1 evaluation.

    ``intent`` is the cypher_shape string the question maps to (e.g.
    ``"relationship_lookup"``). ``expected_top1_pattern_id`` is the
    hand-picked oracle for the top-1 plan; the harness compares the
    cost model's predicted top-1 against this string.
    """

    fixture_id: str
    question: str
    workspace_id: str = "fixture-gopts"
    populated_db: str = "neo4j"
    intent: str = "neighbors"
    expected_top1_pattern_id: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------


def top1_accuracy(predicted: Sequence[str], oracle: Sequence[str]) -> float:
    """1.0 iff predicted[0] == oracle[0] (both non-empty). 0.0 otherwise."""
    if not predicted or not oracle:
        return 0.0
    return 1.0 if predicted[0] == oracle[0] else 0.0


def ndcg_at_k(predicted: Sequence[str], oracle: Sequence[str], k: int) -> float:
    """Standard NDCG@K.

    Relevance of an item = N - oracle_rank(item), so the top of the
    oracle has the highest relevance. Items not in the oracle get 0
    relevance.

    Returns 0.0 for empty inputs or k <= 0. Returns 1.0 only when the
    predicted prefix matches the oracle prefix exactly.
    """
    if not predicted or not oracle or k <= 0:
        return 0.0
    k = min(k, len(predicted), len(oracle))
    n = len(oracle)
    oracle_rank: Dict[str, int] = {item: i for i, item in enumerate(oracle)}

    def rel(item: str) -> int:
        rank = oracle_rank.get(item)
        if rank is None:
            return 0
        return max(0, n - rank)

    dcg = sum(rel(predicted[i]) / math.log2(i + 2) for i in range(k))
    idcg = sum(rel(oracle[i]) / math.log2(i + 2) for i in range(k))
    return dcg / idcg if idcg > 0 else 0.0


def kendall_tau(a: Sequence[str], b: Sequence[str]) -> float:
    """Kendall's tau-a over the intersection of two rankings.

    Returns 1.0 when fewer than two items are shared (degenerate ties
    are reported as agreement so trivially-singleton fixtures don't
    pull the average down). For two or more shared items, returns
    (P - Q) / (n(n-1)/2) where P, Q are concordant and discordant
    pairs respectively.
    """
    common = [item for item in a if item in b]
    if len(common) < 2:
        return 1.0
    a_pos = {item: a.index(item) for item in common}
    b_pos = {item: b.index(item) for item in common}
    concordant = discordant = 0
    n = len(common)
    for i in range(n):
        for j in range(i + 1, n):
            x, y = common[i], common[j]
            sign_a = a_pos[x] - a_pos[y]
            sign_b = b_pos[x] - b_pos[y]
            if sign_a * sign_b > 0:
                concordant += 1
            elif sign_a * sign_b < 0:
                discordant += 1
    total_pairs = n * (n - 1) // 2
    return (concordant - discordant) / total_pairs if total_pairs else 1.0


# ---------------------------------------------------------------------------
# Harness types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureResult:
    fixture_id: str
    candidate_count: int
    predicted_ranking: Tuple[str, ...]
    oracle_ranking: Tuple[str, ...]
    top1_match: bool
    ndcg: float
    tau: float
    notes: str = ""


@dataclass(frozen=True)
class Layer1Report:
    """Aggregate ranking-quality metrics across a fixture suite."""

    fixture_results: Tuple[FixtureResult, ...]
    avg_top1_accuracy: float
    avg_ndcg_at_k: float
    avg_kendall_tau: float
    k: int

    @property
    def total_fixtures(self) -> int:
        return len(self.fixture_results)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_fixtures": self.total_fixtures,
            "k": self.k,
            "avg_top1_accuracy": self.avg_top1_accuracy,
            "avg_ndcg_at_k": self.avg_ndcg_at_k,
            "avg_kendall_tau": self.avg_kendall_tau,
            "fixtures": [
                {
                    "fixture_id": fr.fixture_id,
                    "candidate_count": fr.candidate_count,
                    "predicted_ranking": list(fr.predicted_ranking),
                    "oracle_ranking": list(fr.oracle_ranking),
                    "top1_match": fr.top1_match,
                    "ndcg": fr.ndcg,
                    "tau": fr.tau,
                }
                for fr in self.fixture_results
            ],
        }


# Oracle producer: receives a fixture and the candidate PatternSpec list,
# returns the pattern_ids ordered by oracle (cheapest/best first). Unit
# tests stub this with a deterministic function; live runs wrap a
# Neo4j PROFILE collector.
OracleFn = Callable[[GoptsFixture, List[PatternSpec]], Sequence[str]]


# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------


def load_fixtures(directory: Path) -> List[GoptsFixture]:
    """Load every ``*.yaml`` fixture in ``directory`` into GoptsFixture.

    Each YAML file is one fixture; the file stem becomes the
    ``fixture_id`` when absent in the file body. Unknown YAML keys are
    silently ignored so fixtures can carry Layer-2/Layer-3 metadata
    without breaking the Layer-1 loader.
    """
    import yaml  # local import — yaml is a soft dep used elsewhere

    fixtures: List[GoptsFixture] = []
    for path in sorted(directory.glob("*.yaml")):
        with open(path, "r", encoding="utf-8") as fh:
            body = yaml.safe_load(fh) or {}
        fixture_id = body.get("fixture_id") or path.stem
        fixtures.append(
            GoptsFixture(
                fixture_id=fixture_id,
                question=str(body.get("question") or ""),
                workspace_id=str(body.get("workspace_id") or "fixture-gopts"),
                populated_db=str(body.get("populated_db") or "neo4j"),
                intent=str(body.get("intent") or "neighbors"),
                expected_top1_pattern_id=str(body.get("expected_top1_pattern_id") or ""),
                notes=str(body.get("notes") or ""),
            )
        )
    return fixtures


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def run_layer1(
    fixtures: Sequence[GoptsFixture],
    *,
    oracle_fn: OracleFn,
    index_stats: Optional[Dict[str, Any]] = None,
    coefficients: Optional[Dict[str, float]] = None,
    k: int = 4,
) -> Layer1Report:
    """Evaluate cost-model ranking quality across a fixture suite.

    For each fixture: enumerate candidates via G2's pattern_catalog,
    score them with cost_model, build the predicted ranking, ask
    ``oracle_fn`` for the oracle ranking, and compute per-fixture
    metrics. Returns aggregates across the suite.

    Fixtures whose intent has no registered patterns are dropped from
    the report (they would silently always score 0 otherwise — surface
    them in the missing-fixtures count instead).
    """
    results: List[FixtureResult] = []
    for fixture in fixtures:
        candidates = pattern_catalog.enumerate_for_shape(fixture.intent)
        if not candidates:
            continue
        ranked = cost_model.rank_candidates(
            candidates,
            index_stats=index_stats,
            coefficients=coefficients,
        )
        predicted_ids = tuple(spec.pattern_id for spec, _ in ranked)
        oracle_ids = tuple(oracle_fn(fixture, candidates))
        results.append(
            FixtureResult(
                fixture_id=fixture.fixture_id,
                candidate_count=len(candidates),
                predicted_ranking=predicted_ids,
                oracle_ranking=oracle_ids,
                top1_match=top1_accuracy(predicted_ids, oracle_ids) == 1.0,
                ndcg=ndcg_at_k(predicted_ids, oracle_ids, k),
                tau=kendall_tau(predicted_ids, oracle_ids),
                notes=fixture.notes,
            )
        )

    if not results:
        return Layer1Report(
            fixture_results=(),
            avg_top1_accuracy=0.0,
            avg_ndcg_at_k=0.0,
            avg_kendall_tau=0.0,
            k=k,
        )

    n = len(results)
    return Layer1Report(
        fixture_results=tuple(results),
        avg_top1_accuracy=sum(1.0 if r.top1_match else 0.0 for r in results) / n,
        avg_ndcg_at_k=sum(r.ndcg for r in results) / n,
        avg_kendall_tau=sum(r.tau for r in results) / n,
        k=k,
    )


# ---------------------------------------------------------------------------
# Oracle producers
# ---------------------------------------------------------------------------


def make_expected_top1_oracle_fn() -> OracleFn:
    """Oracle: each fixture declares ``expected_top1_pattern_id`` and the
    rest of the order follows insertion. Useful for fixtures where the
    user knows which pattern *should* win but doesn't have PROFILE data.

    Patterns not listed in the fixture's expected position stay in
    candidate-list order after the expected top-1.
    """

    def oracle(fixture: GoptsFixture, candidates: List[PatternSpec]) -> Sequence[str]:
        expected = fixture.expected_top1_pattern_id
        ids = [c.pattern_id for c in candidates]
        if not expected or expected not in ids:
            return ids
        rest = [pid for pid in ids if pid != expected]
        return [expected] + rest

    return oracle


def make_profile_oracle_fn(
    graph_store: Any,
    *,
    build_cypher_fn: Callable[[GoptsFixture, PatternSpec], Tuple[str, Dict[str, Any]]],
) -> OracleFn:
    """Oracle: rank candidates by Neo4j ``PROFILE`` db_hits.

    Requires a populated graph_store; for unit testing use
    ``make_expected_top1_oracle_fn``. ``build_cypher_fn`` is a callback
    that turns (fixture, pattern) into a (cypher, params) pair the
    oracle can PROFILE.
    """

    def oracle(fixture: GoptsFixture, candidates: List[PatternSpec]) -> Sequence[str]:
        scored: List[Tuple[PatternSpec, int]] = []
        for pattern in candidates:
            try:
                cypher, params = build_cypher_fn(fixture, pattern)
            except Exception:
                continue
            db_hits = _profile_db_hits(graph_store, fixture, cypher, params)
            scored.append((pattern, db_hits))
        scored.sort(key=lambda item: (item[1], item[0].pattern_id))
        return [spec.pattern_id for spec, _ in scored]

    return oracle


def _profile_db_hits(
    graph_store: Any,
    fixture: GoptsFixture,
    cypher: str,
    params: Dict[str, Any],
) -> int:
    """Run ``EXPLAIN`` or ``PROFILE`` and extract total db_hits.

    Best-effort: returns a large sentinel value on failure so a failing
    candidate sorts last instead of incorrectly winning.
    """
    sentinel = 10**12
    try:
        with graph_store._driver.session(database=fixture.populated_db) as session:
            result = session.run(f"PROFILE {cypher}", **params)
            summary = result.consume()
            plan = summary.profile or summary.plan
            if plan is None:
                return sentinel
            return _walk_plan_for_db_hits(plan)
    except Exception:
        return sentinel


def _walk_plan_for_db_hits(plan: Any) -> int:
    """Recursive sum of ``dbHits`` across a Neo4j ProfilePlan tree."""
    total = 0
    args = getattr(plan, "arguments", None) or {}
    total += int(args.get("DbHits") or args.get("dbHits") or 0)
    for child in getattr(plan, "children", None) or []:
        total += _walk_plan_for_db_hits(child)
    return total
