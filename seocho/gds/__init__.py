"""Safe GDS session helpers — projection lifecycle + run metadata.

Closes seocho-xuof.

Background
----------
Graph Data Science procedures (``gds.*``) are powerful but fragile in
practice:

- An orphan projection silently consumes Java heap until the DB restarts.
- The same algorithm with different ``seed`` / ``tolerance`` gives different
  results — without ``GDSRunMeta`` we can't tell *which* run wrote
  ``community_id`` last.
- Memory blow-ups happen when a projection is materialized without first
  calling ``gds.graph.project.estimate``.

This module wraps the safe operating pattern from
``examples/teaching/chapter-02-gds-engineering.md`` (the Ch 2 appendix)
into a context manager:

::

    from seocho.gds import gds_session, MetricSpec

    with gds_session(graph_store, name="ch02-quality", database="neo4j") as g:
        g.project_cypher(
            node_query="MATCH (e) WHERE NOT e:Source AND NOT e:Chunk RETURN elementId(e) AS id",
            rel_query="MATCH (e1)<-[:MENTIONS]-(c:Chunk)-[:MENTIONS]->(e2) "
                      "WHERE elementId(e1) < elementId(e2) RETURN elementId(e1) AS source, elementId(e2) AS target",
            estimate_ok=True,                           # checks .estimate() first
        )
        deg = g.metric(MetricSpec.DEGREE, top_k=10)
        sim = g.metric(MetricSpec.NODE_SIMILARITY, top_k=10)
        g.louvain(write_property="community_id", seed=42)   # auto-writes GDSRunMeta
    # projection auto-dropped on __exit__ even on exception
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public spec
# ---------------------------------------------------------------------------


class MetricSpec(str, Enum):
    """Built-in metrics exposed by :class:`GDSSession.metric`."""

    DEGREE = "degree"
    NODE_SIMILARITY = "nodeSimilarity"
    CLUSTERING = "localClusteringCoefficient"
    LINK_PREDICTION = "alpha.linkprediction.adamicAdar"


class GDSMemoryError(RuntimeError):
    """Raised when the estimate gate refuses to project."""


@dataclass
class GDSEstimate:
    """Result of ``gds.graph.project.estimate``."""

    node_count: int
    relationship_count: int
    bytes_min: int
    bytes_max: int
    required_memory: str

    def fits(self, heap_bytes: int, *, max_fraction: float = 0.30) -> bool:
        return self.bytes_max <= heap_bytes * max_fraction


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class GDSSession:
    """Lifecycle-aware GDS helper. Use via :func:`gds_session` context manager."""

    def __init__(
        self,
        graph_store: Any,
        *,
        name: str,
        database: Optional[str] = None,
    ) -> None:
        self.graph_store = graph_store
        self.name = name
        self.database = database
        self._projected = False

    # -- Cypher plumbing ---------------------------------------------------

    def _run(self, cypher: str, **params: Any) -> List[Dict[str, Any]]:
        for method in ("execute_write", "execute_read", "read", "run"):
            fn = getattr(self.graph_store, method, None)
            if callable(fn):
                try:
                    return fn(cypher, params, database=self.database)
                except TypeError:
                    try:
                        return fn(cypher, params)
                    except TypeError:
                        return fn(cypher)

        driver = getattr(self.graph_store, "driver", None) or getattr(
            self.graph_store, "_driver", None
        )
        if driver is None:
            raise RuntimeError(
                "graph_store cannot run Cypher: no execute_write/read/run/.driver"
            )
        with driver.session(database=self.database) as s:
            return s.run(cypher, **params).data()

    # -- Estimate / project ------------------------------------------------

    def estimate(self, node_query: str, rel_query: str) -> GDSEstimate:
        rows = self._run(
            """
            CALL gds.graph.project.cypher.estimate($name, $nodeQ, $relQ)
            YIELD nodeCount, relationshipCount, bytesMin, bytesMax, requiredMemory
            RETURN nodeCount, relationshipCount, bytesMin, bytesMax, requiredMemory
            """,
            name=self.name,
            nodeQ=node_query,
            relQ=rel_query,
        )
        if not rows:
            raise GDSMemoryError("gds.graph.project.cypher.estimate returned no rows")
        r = rows[0]
        return GDSEstimate(
            node_count=int(r["nodeCount"]),
            relationship_count=int(r["relationshipCount"]),
            bytes_min=int(r["bytesMin"]),
            bytes_max=int(r["bytesMax"]),
            required_memory=str(r["requiredMemory"]),
        )

    def project_cypher(
        self,
        *,
        node_query: str,
        rel_query: str,
        estimate_ok: bool = False,
        heap_bytes: Optional[int] = None,
        max_fraction: float = 0.30,
    ) -> GDSEstimate:
        """Project a graph from custom Cypher.

        When ``estimate_ok=True`` and ``heap_bytes`` is supplied, the call
        refuses to project if ``bytes_max > heap_bytes * max_fraction``
        (default 30%) — the same safety threshold the curriculum recommends.
        """
        est = self.estimate(node_query, rel_query)
        if estimate_ok and heap_bytes is not None and not est.fits(heap_bytes, max_fraction=max_fraction):
            raise GDSMemoryError(
                f"projection bytes_max={est.bytes_max} exceeds {max_fraction*100:.0f}% of heap "
                f"({heap_bytes}); aborting"
            )

        # Idempotent cleanup: drop any leftover with the same name first.
        try:
            self._run(f"CALL gds.graph.drop($name, false)", name=self.name)
        except Exception:
            pass

        self._run(
            "CALL gds.graph.project.cypher($name, $nodeQ, $relQ)",
            name=self.name,
            nodeQ=node_query,
            relQ=rel_query,
        )
        self._projected = True
        return est

    def drop(self) -> None:
        if not self._projected:
            return
        try:
            self._run("CALL gds.graph.drop($name)", name=self.name)
        except Exception as exc:
            logger.warning("GDS drop %s failed: %s", self.name, exc)
        finally:
            self._projected = False

    # -- Algorithms --------------------------------------------------------

    def metric(self, spec: MetricSpec, **kwargs: Any) -> List[Dict[str, Any]]:
        """Streaming form for read-only metrics. Returns rows of the YIELD."""
        top_k = int(kwargs.pop("top_k", 10))
        if spec is MetricSpec.DEGREE:
            return self._run(
                """
                CALL gds.degree.stream($name) YIELD nodeId, score
                RETURN gds.util.asNode(nodeId).name AS name, score
                ORDER BY score DESC LIMIT $k
                """,
                name=self.name, k=top_k,
            )
        if spec is MetricSpec.NODE_SIMILARITY:
            cutoff = float(kwargs.pop("cutoff", 0.5))
            return self._run(
                """
                CALL gds.nodeSimilarity.stream($name)
                YIELD node1, node2, similarity
                WHERE similarity >= $cutoff
                RETURN gds.util.asNode(node1).name AS a,
                       gds.util.asNode(node2).name AS b, similarity
                ORDER BY similarity DESC LIMIT $k
                """,
                name=self.name, cutoff=cutoff, k=top_k,
            )
        if spec is MetricSpec.CLUSTERING:
            return self._run(
                """
                CALL gds.localClusteringCoefficient.stream($name)
                YIELD nodeId, localClusteringCoefficient AS clustering
                RETURN gds.util.asNode(nodeId).name AS name, clustering
                ORDER BY clustering DESC LIMIT $k
                """,
                name=self.name, k=top_k,
            )
        if spec is MetricSpec.LINK_PREDICTION:
            pair_cap = int(kwargs.pop("pair_cap", 200))
            return self._run(
                """
                MATCH (a), (b)
                WHERE a <> b AND NOT (a)-[:MENTIONS|RELATED_TO]-(b)
                  AND NOT a:Source AND NOT a:Chunk AND NOT b:Source AND NOT b:Chunk
                WITH a, b LIMIT $cap
                RETURN a.name AS a, b.name AS b,
                       gds.alpha.linkprediction.adamicAdar(a, b) AS aa
                ORDER BY aa DESC LIMIT $k
                """,
                cap=pair_cap, k=top_k,
            )
        raise ValueError(f"unknown metric: {spec}")

    def louvain(
        self,
        *,
        write_property: str = "community_id",
        seed: int = 42,
        tolerance: float = 0.0001,
        max_iterations: int = 10,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run Louvain + write community ids + persist :class:`GDSRunMeta`."""
        rows = self._run(
            """
            CALL gds.louvain.write($name, {
              writeProperty: $prop,
              randomSeed: $seed,
              tolerance: $tol,
              maxIterations: $iters
            })
            YIELD communityCount, modularity, nodePropertiesWritten
            RETURN communityCount, modularity, nodePropertiesWritten
            """,
            name=self.name,
            prop=write_property,
            seed=seed,
            tol=tolerance,
            iters=max_iterations,
        )
        result = dict(rows[0]) if rows else {}
        self._write_run_meta(
            algo="louvain",
            write_property=write_property,
            workspace_id=workspace_id,
            extra={
                "modularity": float(result.get("modularity", 0.0)),
                "community_count": int(result.get("communityCount", 0)),
                "seed": seed,
            },
        )
        return result

    # -- Metadata ----------------------------------------------------------

    def _write_run_meta(
        self,
        *,
        algo: str,
        write_property: Optional[str],
        workspace_id: Optional[str],
        extra: Mapping[str, Any],
    ) -> None:
        params = {
            "algo": algo,
            "ws": workspace_id or "",
            "prop": write_property or "",
            "name": self.name,
            "ts": datetime.now(timezone.utc).isoformat(),
            "extra": dict(extra),
        }
        try:
            self._run(
                """
                MERGE (m:GDSRunMeta {algo: $algo, workspace_id: $ws, write_property: $prop})
                SET m.last_run_at = datetime($ts),
                    m.graph_name = $name,
                    m.extra = $extra
                """,
                **params,
            )
        except Exception as exc:
            logger.warning("GDSRunMeta write skipped: %s", exc)


# ---------------------------------------------------------------------------
# Context-manager entry point
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def gds_session(graph_store: Any, *, name: str, database: Optional[str] = None) -> Iterable[GDSSession]:
    """Context manager that guarantees projection cleanup on exit.

    See module docstring for usage.
    """
    s = GDSSession(graph_store, name=name, database=database)
    try:
        yield s
    finally:
        s.drop()


__all__ = [
    "MetricSpec",
    "GDSEstimate",
    "GDSMemoryError",
    "GDSSession",
    "gds_session",
]
