"""Federated read over the department databases — composite OR client fan-out.

DozerDB 5.26 ships multi-database but lists fabric/composite as a v2.0+
roadmap item, while its kernel self-reports "enterprise". So the mode is
decided by a runtime preflight smoke test (00_preflight.py), persisted to
``outputs/mode.json``, and every reader here branches on it:

- ``composite``: one query against the composite DB using
  ``CALL () { USE <comp>.<alias> ... RETURN ... UNION ALL ... }`` — the
  Neo4j-docs federation pattern, the demo's headline moment when available.
- ``fanout`` (primary/expected): the same logical read as N driver sessions,
  one per department DB, unioned client-side.

Both return identical record shapes, so everything downstream (staging,
GDS resolution, survivorship) is mode-agnostic.

§8 safety: database names and composite aliases are validated against the
Neo4j-5 name pattern before any Cypher interpolation; reads are read-only;
node identity travels as ``elementId()``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

OUTPUTS_DIR = Path(__file__).resolve().parents[1] / "outputs"
MODE_FILE = "mode.json"

# Neo4j 5 database-name rule (mirrors seocho.store.graph.validate_database_name);
# also applied to composite aliases since each becomes a Cypher token.
_NAME_RE = re.compile(r"^[a-z][a-z0-9]{2,62}$")

# Infrastructure labels that are not business entities.
INFRA_LABELS = [
    "Document", "DocumentVersion", "Chunk", "Section", "Source",
    "GDSRunMeta", "EntityProxy", "SourceRecord",
]


def _validated(name: str, *, what: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"invalid {what} {name!r} (must match {_NAME_RE.pattern}) — §8")
    return name


# ---------------------------------------------------------------------------
# Mode persistence (written by 00_preflight.py)
# ---------------------------------------------------------------------------

def write_mode(mode: str, detail: Dict[str, Any], *, outputs_dir: Path = OUTPUTS_DIR) -> Path:
    if mode not in ("composite", "fanout"):
        raise ValueError(f"unknown federation mode {mode!r}")
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / MODE_FILE
    path.write_text(json.dumps({"mode": mode, **detail}, indent=2) + "\n", encoding="utf-8")
    return path


def read_mode(*, outputs_dir: Path = OUTPUTS_DIR) -> str:
    """Read the preflight-decided mode. No default: an unset mode means the
    preflight was skipped, and silently assuming one would hide that (§20.2)."""
    path = outputs_dir / MODE_FILE
    if not path.is_file():
        raise RuntimeError(f"{path} missing — run 00_preflight.py first")
    mode = json.loads(path.read_text(encoding="utf-8")).get("mode")
    if mode not in ("composite", "fanout"):
        raise RuntimeError(f"corrupt {path}: mode={mode!r}")
    return mode


# ---------------------------------------------------------------------------
# Composite lifecycle + smoke test (raw system-db sessions; NEVER through
# ensure_database(), which would create a *standard* DB of the same name)
# ---------------------------------------------------------------------------

def composite_smoke_test(driver) -> tuple[bool, str]:
    """Can this DBMS create a composite database? Creates + drops ``compsmoke``."""
    try:
        with driver.session(database="system") as s:
            s.run("CREATE COMPOSITE DATABASE compsmoke IF NOT EXISTS WAIT").consume()
            rows = s.run(
                "SHOW DATABASES YIELD name, type WHERE name = 'compsmoke' "
                "RETURN name, type").data()
            s.run("DROP COMPOSITE DATABASE compsmoke IF EXISTS").consume()
        if rows and rows[0].get("type") == "composite":
            return True, "composite database created and dropped OK"
        return False, f"compsmoke created but type={rows[0].get('type') if rows else '<absent>'}"
    except Exception as exc:
        # Expected on DozerDB 5.26 (fabric is roadmap): record why, fall back.
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def create_composite(driver, *, composite: str, aliases: Dict[str, str]) -> None:
    """CREATE COMPOSITE DATABASE + one alias per department DB. Idempotent."""
    comp = _validated(composite, what="composite name")
    with driver.session(database="system") as s:
        s.run(f"CREATE COMPOSITE DATABASE {comp} IF NOT EXISTS WAIT").consume()
        for alias, db in aliases.items():
            a = _validated(alias, what="composite alias")
            d = _validated(db, what="database name")
            s.run(f"CREATE ALIAS {comp}.{a} IF NOT EXISTS FOR DATABASE {d}").consume()


def drop_composite(driver, *, composite: str, aliases: Dict[str, str]) -> None:
    comp = _validated(composite, what="composite name")
    with driver.session(database="system") as s:
        for alias in aliases:
            a = _validated(alias, what="composite alias")
            s.run(f"DROP ALIAS {comp}.{a} IF EXISTS FOR DATABASE").consume()
        s.run(f"DROP COMPOSITE DATABASE {comp} IF EXISTS").consume()


# ---------------------------------------------------------------------------
# Federated reads
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Department:
    name: str        # "risk" | "research" | "compliance" (also the composite alias)
    database: str    # e.g. "mdmrisk"
    model: str       # e.g. "DeepSeek-V3.1"

    def __post_init__(self) -> None:
        _validated(self.name, what="department/alias name")
        _validated(self.database, what="database name")


_ENTITY_READ = """
MATCH (n)
WHERE n.name IS NOT NULL AND n.value IS NULL
  AND NOT any(l IN labels(n) WHERE l IN $infra)
RETURN labels(n) AS labels, n.name AS name, elementId(n) AS eid,
       n._workspace_id AS ws, properties(n) AS props
"""

# Metric facts = ANY value-bearing node (per the tier-1 property discipline,
# figures live in `value`); models disagree wildly on metric LABELS
# (FinancialMetric vs EPS/Revenue/NetIncome subtypes vs generic Entity), so
# matching on a label would silently drop most of two departments' facts.
# Company attribution rides on `_workspace_id` (one workspace = one filing
# case), since extractors often leave metrics unconnected to the filer node.
_METRIC_READ = """
MATCH (m)
WHERE m.value IS NOT NULL AND m.name IS NOT NULL
  AND NOT any(l IN labels(m) WHERE l IN $infra)
OPTIONAL MATCH (e)-[r]-(m)
WHERE e.name IS NOT NULL AND e.value IS NULL
  AND NOT type(r) IN ['MENTIONS']
  AND NOT any(l IN labels(e) WHERE l IN $infra)
WITH m, collect(DISTINCT e.name)[0..3] AS linked
RETURN labels(m) AS labels, m.name AS metric, m.value AS value,
       m.period AS period, m.basis AS basis, elementId(m) AS metric_eid,
       m._workspace_id AS ws, linked AS linked_entities
"""


def case_from_ws(ws: object, dept_name: str) -> str:
    """``mdm-<dept>-<case_id>`` -> ``<case_id>`` (else the raw value)."""
    text = str(ws or "")
    prefix = f"mdm-{dept_name}-"
    return text[len(prefix):] if text.startswith(prefix) else text


def _entity_record(row: Dict[str, Any], dept: Department) -> Dict[str, Any]:
    return {
        "name": row["name"],
        "labels": [l for l in (row["labels"] or []) if l not in INFRA_LABELS],
        "eid": row["eid"],
        "ws": row.get("ws"),
        "case_id": case_from_ws(row.get("ws"), dept.name),
        "src_db": dept.database,
        "dept": dept.name,
        "model": dept.model,
        "props": {k: v for k, v in (row.get("props") or {}).items()
                  if not str(k).startswith("_")},
    }


def _metric_record(row: Dict[str, Any], dept: Department) -> Dict[str, Any]:
    return {
        "labels": [l for l in (row["labels"] or []) if l not in INFRA_LABELS],
        "metric": row["metric"],
        "value": row["value"],
        "period": row["period"],
        "basis": row["basis"],
        "metric_eid": row["metric_eid"],
        "ws": row.get("ws"),
        "case_id": case_from_ws(row.get("ws"), dept.name),
        "linked_entities": row.get("linked_entities") or [],
        "src_db": dept.database,
        "dept": dept.name,
        "model": dept.model,
    }


def fanout_read(graph_store, departments: List[Department]
                ) -> tuple[List[Dict], List[Dict]]:
    """Primary path: one read-only session per department DB, client-side union."""
    entities: List[Dict] = []
    metrics: List[Dict] = []
    for dept in departments:
        for row in graph_store.query(_ENTITY_READ, params={"infra": INFRA_LABELS},
                                     database=dept.database) or []:
            entities.append(_entity_record(row, dept))
        for row in graph_store.query(_METRIC_READ, params={"infra": INFRA_LABELS},
                                     database=dept.database) or []:
            metrics.append(_metric_record(row, dept))
    return entities, metrics


def _composite_union(composite: str, departments: List[Department], body: str,
                     returns: str) -> str:
    comp = _validated(composite, what="composite name")
    branches = []
    for dept in departments:
        a = _validated(dept.name, what="composite alias")
        branches.append(
            f"  USE {comp}.{a}\n{body}\n  RETURN {returns}, '{a}' AS dept_alias")
    joined = "\n  UNION ALL\n".join(branches)
    return f"CALL () {{\n{joined}\n}}\nRETURN *"


def composite_read(driver, *, composite: str, departments: List[Department]
                   ) -> tuple[List[Dict], List[Dict]]:
    """Composite path: single federated query per read, USE-union over aliases."""
    by_alias = {d.name: d for d in departments}
    ent_q = _composite_union(
        composite, departments,
        body=("  MATCH (n) WHERE n.name IS NOT NULL AND n.value IS NULL "
              "AND NOT any(l IN labels(n) WHERE l IN $infra)"),
        returns=("labels(n) AS labels, n.name AS name, elementId(n) AS eid, "
                 "n._workspace_id AS ws, properties(n) AS props"),
    )
    met_q = _composite_union(
        composite, departments,
        body=("  MATCH (m) WHERE m.value IS NOT NULL AND m.name IS NOT NULL "
              "AND NOT any(l IN labels(m) WHERE l IN $infra) "
              "OPTIONAL MATCH (e)-[r]-(m) "
              "WHERE e.name IS NOT NULL AND e.value IS NULL "
              "AND NOT type(r) IN ['MENTIONS'] "
              "AND NOT any(l IN labels(e) WHERE l IN $infra) "
              "WITH m, collect(DISTINCT e.name)[0..3] AS linked"),
        returns=("labels(m) AS labels, m.name AS metric, m.value AS value, "
                 "m.period AS period, m.basis AS basis, "
                 "elementId(m) AS metric_eid, m._workspace_id AS ws, "
                 "linked AS linked_entities"),
    )
    entities: List[Dict] = []
    metrics: List[Dict] = []
    with driver.session(database=composite) as s:
        for row in s.run(ent_q, infra=INFRA_LABELS).data():
            entities.append(_entity_record(row, by_alias[row["dept_alias"]]))
        for row in s.run(met_q, infra=INFRA_LABELS).data():
            metrics.append(_metric_record(row, by_alias[row["dept_alias"]]))
    return entities, metrics
