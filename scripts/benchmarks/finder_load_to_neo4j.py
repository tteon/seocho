#!/usr/bin/env python3
"""Load all FinDER phase experiment LadyBug graphs into a single Neo4j DB.

Each phase × case lbug is read and inserted preserving original label + props,
with three injected meta-properties so the merged graph stays sliceable:
  _phase     — e.g. 'P0', 'P1A'
  _case_id   — FinDER case id
  _src_lbug  — origin file name

Relationships are loaded with the same meta-props and proper endpoints
resolved via a lbug-local id map.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for path in (SRC, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from examples.finder.lib import bench_common as bc  # noqa: E402
from seocho.store.graph import LadybugGraphStore  # noqa: E402

LBUG_DIR = bc.DEFAULT_LBUG_DIR


def parse_lbug_name(name: str) -> tuple[str, str, str]:
    """e.g. 'P0_4af93b03_treatment.lbug' -> ('P0', '4af93b03', 'treatment')."""
    stem = name.replace(".lbug", "")
    parts = stem.split("_")
    return parts[0], parts[1], parts[2] if len(parts) > 2 else ""


def lbug_id_key(lid) -> str:
    """LadyBug returns id() as {'offset':..., 'table':...}. Serialize stably."""
    if isinstance(lid, dict):
        return f"{lid.get('table',0)}:{lid.get('offset',0)}"
    return str(lid)


def clean_props(
    props: dict, phase: str, case_id: str, src: str, workspace_id: str
) -> dict:
    """Strip Ladybug internal fields, inject meta. Drop None values for tidy props."""
    EXCLUDE = {"_ID", "_LABEL"}
    out: dict = {}
    for k, v in props.items():
        if k in EXCLUDE:
            continue
        if v is None:
            continue
        # Neo4j doesn't accept dicts/lists of dicts as property values; coerce
        if isinstance(v, (list, tuple)):
            out[k] = [str(x) if isinstance(x, (dict, list, tuple)) else x for x in v]
        elif isinstance(v, dict):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    out["_phase"] = phase
    out["_case_id"] = case_id
    out["_src_lbug"] = src
    # §6.1 — propagate workspace_id from the loader to every persisted node
    if workspace_id and not out.get("_workspace_id"):
        out["_workspace_id"] = workspace_id
    return out


def safe_label(label: str) -> str:
    """Neo4j label sanity: alnum+underscore only."""
    return re.sub(r"[^A-Za-z0-9_]", "_", label or "Entity")


def safe_reltype(rt: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", rt or "RELATED_TO").upper()


def load_one(
    driver, lbug_path: Path, batch: int = 200, *, workspace_prefix: str = "finder-load"
) -> dict:
    phase, case_id, variant = parse_lbug_name(lbug_path.name)
    workspace_id = bc.workspace_id_for(
        phase, case_id, variant or "loaded", prefix=workspace_prefix
    )
    g = LadybugGraphStore(str(lbug_path))
    stats = {
        "phase": phase,
        "case_id": case_id,
        "lbug": lbug_path.name,
        "variant": variant,
        "workspace_id": workspace_id,
        "nodes": 0,
        "rels": 0,
        "labels": Counter(),
        "rel_types": Counter(),
        "errors": [],
    }
    try:
        node_rows = g.query("MATCH (n) RETURN n, id(n) AS lid, labels(n) AS lbl") or []
        rel_rows = (
            g.query(
                "MATCH (a)-[r]->(b) RETURN id(a) AS sid, id(b) AS tid, "
                "type(r) AS rt, properties(r) AS rp"
            )
            or []
        )
    except Exception as e:
        stats["errors"].append(f"read err: {type(e).__name__}: {e}")
        g.close()
        return stats
    g.close()

    # Insert nodes — capture mapping lbug_id_key -> neo4j elementId
    lid_to_eid: dict[str, str] = {}
    with driver.session() as session:
        for i in range(0, len(node_rows), batch):
            chunk = node_rows[i : i + batch]
            params = []
            for row in chunk:
                lbl_raw = row.get("lbl") or ""
                # Ladybug returns label as scalar; ensure list & sanitize
                if isinstance(lbl_raw, str):
                    labels = [safe_label(lbl_raw)] if lbl_raw else ["Entity"]
                elif isinstance(lbl_raw, list):
                    labels = [safe_label(l) for l in lbl_raw] or ["Entity"]
                else:
                    labels = ["Entity"]
                props = clean_props(
                    row.get("n") or {}, phase, case_id, lbug_path.name, workspace_id
                )
                # Use the first label only for clean Neo4j label; keep all in _all_labels
                primary = labels[0]
                props["_all_labels"] = labels
                props["_lbug_lid"] = lbug_id_key(row.get("lid"))
                params.append(
                    {"label": primary, "props": props, "lid_key": props["_lbug_lid"]}
                )
                stats["labels"][primary] += 1
                stats["nodes"] += 1

            # Group by primary label to avoid one giant UNWIND with different labels
            by_label: dict[str, list] = {}
            for p in params:
                by_label.setdefault(p["label"], []).append(p)
            for lbl, items in by_label.items():
                q = (
                    f"UNWIND $rows AS row "
                    f"CREATE (n:`{lbl}`) "
                    f"SET n = row.props "
                    f"RETURN row.lid_key AS lk, elementId(n) AS eid"
                )
                try:
                    result = session.run(q, rows=items)
                    for r in result:
                        lid_to_eid[r["lk"]] = r["eid"]
                except Exception as e:
                    stats["errors"].append(
                        f"node insert err ({lbl}): {type(e).__name__}: {str(e)[:160]}"
                    )

        # Insert relationships in batches per rel_type
        rel_by_type: dict[str, list] = {}
        for row in rel_rows:
            sid_key = lbug_id_key(row.get("sid"))
            tid_key = lbug_id_key(row.get("tid"))
            if sid_key not in lid_to_eid or tid_key not in lid_to_eid:
                continue
            rt = safe_reltype(row.get("rt"))
            stats["rel_types"][rt] += 1
            rp = row.get("rp") or {}
            rp = {
                k: v for k, v in rp.items() if v is not None and not isinstance(v, dict)
            }
            rp["_phase"] = phase
            rp["_case_id"] = case_id
            rp["_workspace_id"] = workspace_id
            rel_by_type.setdefault(rt, []).append(
                {
                    "sid": lid_to_eid[sid_key],
                    "tid": lid_to_eid[tid_key],
                    "props": rp,
                }
            )

        for rt, rows in rel_by_type.items():
            for i in range(0, len(rows), batch):
                chunk = rows[i : i + batch]
                q = (
                    "UNWIND $rows AS row "
                    "MATCH (a) WHERE elementId(a) = row.sid "
                    "MATCH (b) WHERE elementId(b) = row.tid "
                    f"CREATE (a)-[r:`{rt}`]->(b) "
                    "SET r = row.props"
                )
                try:
                    session.run(q, rows=chunk)
                    stats["rels"] += len(chunk)
                except Exception as e:
                    stats["errors"].append(
                        f"rel insert err ({rt}): {type(e).__name__}: {str(e)[:160]}"
                    )
    return stats


# Auto-created indexes after load (T2.7)
_AUTO_INDEXES = [
    ("node_phase", "CREATE INDEX node_phase IF NOT EXISTS FOR (n:_) ON (n._phase)"),
    (
        "node_case_id",
        "CREATE INDEX node_case_id IF NOT EXISTS FOR (n:_) ON (n._case_id)",
    ),
    (
        "node_workspace",
        "CREATE INDEX node_workspace IF NOT EXISTS FOR (n:_) ON (n._workspace_id)",
    ),
    (
        "node_source_id",
        "CREATE INDEX node_source_id IF NOT EXISTS FOR (n:_) ON (n._source_id)",
    ),
    ("node_name", "CREATE INDEX node_name IF NOT EXISTS FOR (n:_) ON (n.name)"),
]
# Fulltext index needs a label list; we'll resolve it after seeing actual labels.


def _create_indexes(session, labels: list[str]) -> list[dict]:
    """Create the standardized index set after loading.

    Neo4j 5+ supports schema-less indexes only via per-label syntax, so we
    create per-label property indexes for each meta-property. The fulltext
    index covers all domain labels (name, description).
    """
    results = []
    domain_labels = [
        l
        for l in labels
        if l not in {"Chunk", "Document", "DocumentVersion", "Section"}
    ]
    for prop in ("_phase", "_case_id", "_workspace_id", "_source_id", "name"):
        for lbl in labels:
            idx_name = f"node_{lbl}_{prop.lstrip('_')}"
            stmt = (
                f"CREATE INDEX {idx_name} IF NOT EXISTS FOR (n:`{lbl}`) ON (n.`{prop}`)"
            )
            try:
                session.run(stmt)
                results.append({"index": idx_name, "ok": True})
            except Exception as exc:
                results.append({"index": idx_name, "ok": False, "err": str(exc)[:120]})
    # Fulltext index on (name, description) across domain labels
    if domain_labels:
        ft_targets = ", ".join(f"`{l}`" for l in domain_labels)
        ft_stmt = (
            f"CREATE FULLTEXT INDEX node_text IF NOT EXISTS "
            f"FOR (n:{ft_targets}) ON EACH [n.name, n.description]"
        )
        try:
            session.run(ft_stmt)
            results.append({"index": "node_text", "ok": True, "kind": "fulltext"})
        except Exception as exc:
            results.append({"index": "node_text", "ok": False, "err": str(exc)[:120]})
    return results


def main() -> int:
    bc.bootstrap(verbose=True)
    uri = os.environ.get("NEO4J_URI") or os.environ.get("BOLT_URL")
    user = os.environ.get("NEO4J_USER") or "neo4j"
    pwd = os.environ.get("NEO4J_PASSWORD")
    if not (uri and pwd):
        raise SystemExit("Missing NEO4J_URI / NEO4J_PASSWORD in env")

    # preflight (Neo4j connectivity required for this step)
    report = bc.preflight(
        strict=True,
        require_moonshot=False,
        require_openai_embed=False,
        require_neo4j=True,
        require_slices=False,
    )
    report.print_table()
    if not report.ok:
        raise SystemExit("preflight failed — fix Neo4j connectivity before running")

    print(f"Neo4j → {uri} as {user}")
    drv = GraphDatabase.driver(uri, auth=(user, pwd))

    with drv.session() as s:
        n0 = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        r0 = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    print(f"before load: nodes={n0} rels={r0}")

    files = sorted(LBUG_DIR.glob("*.lbug"))
    print(f"loading {len(files)} lbug files…")
    workspace_prefix = f"finder-load-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}"

    all_stats = []
    for f in files:
        t0 = time.perf_counter()
        s = load_one(drv, f, workspace_prefix=workspace_prefix)
        elapsed = round(time.perf_counter() - t0, 2)
        print(
            f"  {f.name:55s} nodes={s['nodes']:3d}  rels={s['rels']:3d}  "
            f"labels={dict(s['labels'])}  ws={s['workspace_id'][-20:]}  ({elapsed}s)"
        )
        if s["errors"]:
            for e in s["errors"][:3]:
                print(f"      ! {e}")
        all_stats.append(s)

    with drv.session() as s:
        n1 = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        r1 = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        labels = [
            x["label"]
            for x in s.run(
                "CALL db.labels() YIELD label RETURN label ORDER BY label"
            ).data()
        ]
        rels = [
            x["relationshipType"]
            for x in s.run(
                "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType ORDER BY relationshipType"
            ).data()
        ]
        per_phase = s.run(
            "MATCH (n) RETURN n._phase AS p, count(n) AS c ORDER BY p"
        ).data()
    print(f"\nafter load: nodes={n1} rels={r1}")
    print(f"labels: {labels}")
    print(f"rel types: {rels}")
    print(f"per phase: {per_phase}")

    # T2.7 — auto-create indexes
    print("\ncreating auto indexes…")
    with drv.session() as s:
        index_results = _create_indexes(s, labels)
    ok = sum(1 for r in index_results if r.get("ok"))
    print(f"  {ok}/{len(index_results)} index statements ok")
    drv.close()

    out_dir = ROOT / "outputs/neo4j_load/finder_phase"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "neo4j_uri": uri,
        "workspace_prefix": workspace_prefix,
        "total_nodes": n1,
        "total_rels": r1,
        "labels": labels,
        "rel_types": rels,
        "per_phase": per_phase,
        "indexes_created": index_results,
        "files": [
            {
                "lbug": st["lbug"],
                "phase": st["phase"],
                "case_id": st["case_id"],
                "workspace_id": st["workspace_id"],
                "nodes": st["nodes"],
                "rels": st["rels"],
                "labels": dict(st["labels"]),
                "rel_types": dict(st["rel_types"]),
                "errors": st["errors"],
            }
            for st in all_stats
        ],
    }
    out_path = (
        out_dir / f"load_manifest_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    )
    bc.atomic_write_json(out_path, manifest)
    print(f"\nmanifest → {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
