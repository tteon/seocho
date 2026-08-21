"""Ablation A2 — isolation: cross-tenant leak rate, enforcement OFF vs ON.

Ablation row A2 of the OS study (obsidian wiki/os-ablation-study-design.md,
seocho-76k). The isolation subsystem's axis is: does an adversarial or careless
Cypher, run by a session scoped to tenant "acme", ever return another tenant's
rows? We measure the leak count against a battery of attack shapes, with the
governed enforcement OFF (a bare agent's raw tool) vs ON (the OS's
`enforce_read_workspace_scope` + workspace pin, PR #501/#512).

Real graph, live DozerDB: a two-tenant node set is loaded into the default
database (unique probe label, cleaned up after). Each attack nominally scopes to
``$workspace_id = acme``; a leak is any returned row whose ``_workspace_id`` is
not ``acme``. Ceiling/floor are structural here: OFF is the floor (ungoverned),
a perfectly-scoped query is the ceiling (0 leaks by construction).

Usage:
  python scripts/agentos/ablation_a2_isolation.py --container graphrag-neo4j \
      --out outputs/agentos/ablation_a2_isolation.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from seocho.store.graph import (  # noqa: E402
    WorkspaceFilterMissingError, WorkspaceScopeViolationError,
    enforce_read_workspace_scope)

_LABEL = "_AblA2Node"
_TENANT = "acme"
_OTHER = "globex"

# Each attack is Cypher a session scoped to `acme` might run (or be tricked into
# running). All are shaped to pull cross-tenant rows; the governed path must
# refuse or scope every one. `expects_block` = the ON arm should reject it
# outright (vs scope it to 0 leaks by pinning).
_ATTACKS = [
    {"id": "unscoped_all", "cypher": f"MATCH (n:{_LABEL}) RETURN n"},
    {"id": "or_true",
     "cypher": f"MATCH (n:{_LABEL}) WHERE n._workspace_id = $workspace_id "
               "OR true RETURN n"},
    {"id": "or_1eq1",
     "cypher": f"MATCH (n:{_LABEL}) WHERE n._workspace_id = $workspace_id "
               "OR 1=1 RETURN n"},
    {"id": "wrong_node_binding",
     "cypher": f"MATCH (n:{_LABEL}),(m:{_LABEL}) "
               "WHERE m._workspace_id = $workspace_id RETURN n"},
    {"id": "comment_smuggle",
     "cypher": f"MATCH (n:{_LABEL}) RETURN n /* $workspace_id */"},
    {"id": "properly_scoped",   # the honest control: this SHOULD pass and leak 0
     "cypher": f"MATCH (n:{_LABEL}) WHERE n._workspace_id = $workspace_id "
               "RETURN n"},
]


def auth_of(container: str):
    out = subprocess.check_output(
        ["docker", "inspect", container, "--format",
         "{{range .Config.Env}}{{println .}}{{end}}"]).decode()
    for line in out.splitlines():
        if line.startswith("NEO4J_AUTH="):
            u, p = line[len("NEO4J_AUTH="):].split("/", 1)
            return u, p
    raise SystemExit(f"no NEO4J_AUTH on {container}")


def _leaked(rows: List[Dict[str, Any]]) -> int:
    n = 0
    for r in rows:
        node = r.get("n", r)
        ws = (node.get("_workspace_id") if isinstance(node, dict) else None)
        if ws is not None and ws != _TENANT:
            n += 1
    return n


def run(container: str, uri: str, database: str) -> Dict[str, Any]:
    from neo4j import GraphDatabase

    u, p = auth_of(container)
    drv = GraphDatabase.driver(uri, auth=(u, p))
    results: List[Dict[str, Any]] = []
    try:
        # setup: 3 acme nodes + 3 globex nodes
        with drv.session(database=database) as s:
            s.run(f"MATCH (n:{_LABEL}) DETACH DELETE n")
            for i in range(3):
                s.run(f"CREATE (n:{_LABEL} {{id:$id, _workspace_id:$ws}})",
                      id=f"{_TENANT}-{i}", ws=_TENANT)
                s.run(f"CREATE (n:{_LABEL} {{id:$id, _workspace_id:$ws}})",
                      id=f"{_OTHER}-{i}", ws=_OTHER)

        def raw(cypher, pin):
            params = {"workspace_id": _TENANT} if pin else {}
            with drv.session(database=database,
                             default_access_mode="READ") as s:
                return [rec.data() for rec in s.run(cypher, parameters=params)]

        for atk in _ATTACKS:
            cy = atk["cypher"]
            # OFF (floor): ungoverned. The token still resolves (pin=True) so
            # the query runs, but NO enforcement gate — a bare tool.
            try:
                off_rows = raw(cy, pin=True)
                off_leak = _leaked(off_rows)
                off_note = f"{len(off_rows)} rows"
            except Exception as exc:
                off_leak, off_note = 0, f"error:{type(exc).__name__}"
            # ON: run the enforcement gate first.
            on_blocked, on_reason = False, ""
            try:
                enforce_read_workspace_scope(cy)
            except WorkspaceFilterMissingError:
                on_blocked, on_reason = True, "filter_missing"
            except WorkspaceScopeViolationError as exc:
                on_blocked, on_reason = True, exc.reason
            if on_blocked:
                on_leak, on_note = 0, f"blocked:{on_reason}"
            else:
                on_rows = raw(cy, pin=True)     # passed the gate → run pinned
                on_leak = _leaked(on_rows)
                on_note = f"{len(on_rows)} rows"
            results.append({"attack": atk["id"], "off_leak": off_leak,
                            "off": off_note, "on_leak": on_leak, "on": on_note})
    finally:
        with drv.session(database=database) as s:
            s.run(f"MATCH (n:{_LABEL}) DETACH DELETE n")
        drv.close()

    off_total = sum(r["off_leak"] for r in results)
    on_total = sum(r["on_leak"] for r in results)
    return {"tenant": _TENANT, "other": _OTHER, "attacks": results,
            "off_leaked_total": off_total, "on_leaked_total": on_total,
            "attacks_leaking_off": sum(1 for r in results if r["off_leak"] > 0),
            "attacks_leaking_on": sum(1 for r in results if r["on_leak"] > 0)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--container", default="graphrag-neo4j")
    ap.add_argument("--uri", default="bolt://localhost:7687")
    ap.add_argument("--database", default="neo4j")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    report = run(args.container, args.uri, args.database)
    print("=== A2 isolation: cross-tenant leak, enforcement OFF vs ON ===")
    print(f"  {'attack':22s} {'OFF leak':>9s}  {'ON leak':>8s}   detail")
    for r in report["attacks"]:
        print(f"  {r['attack']:22s} {r['off_leak']:>9d}  {r['on_leak']:>8d}   "
              f"off={r['off']} | on={r['on']}")
    print(f"\n  OFF total leaked rows: {report['off_leaked_total']} "
          f"({report['attacks_leaking_off']}/{len(report['attacks'])} attacks leak)")
    print(f"  ON  total leaked rows: {report['on_leaked_total']} "
          f"({report['attacks_leaking_on']}/{len(report['attacks'])} attacks leak)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
