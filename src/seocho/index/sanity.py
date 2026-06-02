"""Temporal sanity checks for indexed knowledge graphs.

Closes seocho-le4c.

The 5 checks are taken verbatim from
``examples/teaching/chapter-01-property-design.md`` §10 (the curriculum's
property-design appendix). They are:

1. ``future_dated_mentions``    — ``r.extracted_at > now()``
2. ``inverted_temporal_ranges`` — ``r.temporal_range.from > r.temporal_range.to``
3. ``orphan_extractions``       — ``r.extraction_run_id`` missing
4. ``stale_entities_1y``        — ``e.last_seen_at`` older than 365 days *and*
                                  ``e.mention_count`` > 0
5. ``non_monotonic_versions``   — same ``source_id`` whose ``version`` is not
                                  strictly increasing across nodes

Usage
-----

::

    from seocho.index.sanity import run_temporal_checks

    report = run_temporal_checks(graph_store, workspace_id="hardy", database="neo4j")
    print(report.summary())
    report.assert_clean()   # raises TemporalAnomalyError on any violation

The function is intentionally read-only and cheap: each check is a single
aggregating Cypher and the whole pass runs in one session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


class TemporalAnomalyError(RuntimeError):
    """Raised by :meth:`TemporalReport.assert_clean` when any check is non-zero."""


@dataclass
class TemporalReport:
    """Result of a temporal sanity pass."""

    workspace_id: Optional[str]
    database: Optional[str]
    counts: Dict[str, int | str] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return all(isinstance(v, int) and v == 0 for v in self.counts.values())

    def violations(self) -> Dict[str, int]:
        return {k: v for k, v in self.counts.items() if isinstance(v, int) and v > 0}

    def summary(self) -> str:
        marker = "✅" if self.clean else "⚠️"
        head = f"{marker} TemporalReport(workspace_id={self.workspace_id!r}, db={self.database!r})"
        rows = "\n".join(f"  - {k:30s} = {v}" for k, v in self.counts.items())
        return f"{head}\n{rows}"

    def assert_clean(self) -> None:
        if not self.clean:
            raise TemporalAnomalyError(
                f"temporal anomalies detected: {self.violations()}"
            )


# ---------------------------------------------------------------------------
# Cypher catalogue
# ---------------------------------------------------------------------------


def _checks() -> Dict[str, str]:
    """Return the 5 temporal sanity queries.

    Each query MUST return a single row with column ``n`` (integer count).
    Queries are workspace-aware: callers pass ``$workspace_id`` if the graph
    stamps it on every node — when absent the parameter is simply unused.
    """
    return {
        "future_dated_mentions": """
            MATCH (:Chunk)-[r:MENTIONS]->()
            WHERE r.extracted_at IS NOT NULL
              AND r.extracted_at > datetime()
            RETURN count(*) AS n
        """,
        "inverted_temporal_ranges": """
            MATCH ()-[r:RELATED_TO]->()
            WHERE r.temporal_range IS NOT NULL
              AND r.temporal_range.from IS NOT NULL
              AND r.temporal_range.to   IS NOT NULL
              AND r.temporal_range.from > r.temporal_range.to
            RETURN count(*) AS n
        """,
        "orphan_extractions": """
            MATCH ()-[r:MENTIONS]->()
            WHERE r.extraction_run_id IS NULL OR r.extraction_run_id = ''
            RETURN count(*) AS n
        """,
        "stale_entities_1y": """
            MATCH (e)
            WHERE NOT e:Source AND NOT e:Chunk
              AND e.last_seen_at IS NOT NULL
              AND duration.between(e.last_seen_at, datetime()).days > 365
              AND coalesce(e.mention_count, 0) > 0
            RETURN count(*) AS n
        """,
        "non_monotonic_versions": """
            MATCH (s:Source)
            WITH s.source_id AS sid, collect(s.version) AS vs
            WHERE size(vs) > 1
            WITH sid, vs, [i IN range(1, size(vs)-1) WHERE vs[i] <= vs[i-1]] AS bad
            WHERE size(bad) > 0
            RETURN count(*) AS n
        """,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_temporal_checks(
    graph_store: Any,
    *,
    workspace_id: Optional[str] = None,
    database: Optional[str] = None,
    include: Optional[Iterable[str]] = None,
) -> TemporalReport:
    """Run the 5 temporal sanity checks against ``graph_store``.

    Parameters
    ----------
    graph_store:
        An adapter that exposes ``execute_read(cypher: str, params: dict, database: str | None)``
        or, as a fallback, a ``driver`` attribute returning a ``neo4j.Driver``.
        The local engine's :class:`seocho.store.graph.Neo4jGraphStore` qualifies.
    workspace_id:
        Currently informational — stored on the report only. Reserved for the
        day the schema partitions every node by ``workspace_id`` (CLAUDE.md §6.1).
    database:
        Optional database name. When omitted, the store's default is used.
    include:
        Subset of check names to run. ``None`` (default) runs all 5.

    Returns
    -------
    :class:`TemporalReport` — call ``.assert_clean()`` to enforce, or read
    ``.counts`` for individual values.
    """
    queries = _checks()
    if include is not None:
        names = set(include)
        queries = {k: v for k, v in queries.items() if k in names}

    counts: Dict[str, int | str] = {}
    for name, cypher in queries.items():
        try:
            rows = _execute_read(graph_store, cypher, database)
            counts[name] = int(rows[0]["n"]) if rows else 0
        except Exception as exc:  # storage backend error or unsupported feature
            counts[name] = f"<err: {type(exc).__name__}: {str(exc)[:80]}>"

    return TemporalReport(workspace_id=workspace_id, database=database, counts=counts)


def _execute_read(graph_store: Any, cypher: str, database: Optional[str]) -> list[Dict[str, Any]]:
    """Indirection over the various graph-store adapters in this repo.

    Tries the most likely method names in order. The fallback assumes a
    standard neo4j driver attribute.
    """
    for method in ("execute_read", "read", "run_read"):
        fn = getattr(graph_store, method, None)
        if callable(fn):
            try:
                return fn(cypher, database=database)
            except TypeError:
                return fn(cypher)

    driver = getattr(graph_store, "driver", None) or getattr(graph_store, "_driver", None)
    if driver is not None:
        with driver.session(database=database) as s:
            return s.run(cypher).data()

    raise RuntimeError(
        "graph_store does not expose execute_read / read / run_read / .driver — "
        "cannot run temporal sanity checks"
    )


__all__ = [
    "TemporalReport",
    "TemporalAnomalyError",
    "run_temporal_checks",
]
