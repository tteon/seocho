"""Ablation A5 — execution honesty: truncation disclosure, OFF vs ON.

Ablation row A5 of the OS study (wiki/os-ablation-study-design.md, seocho-2ay).
The execution subsystem's honesty axis: when a result is capped, does the tool
SIGNAL it, so a consumer (or the agent) cannot present a partial result as
complete? We compare a bare raw tool (OFF — returns rows, no truncation
metadata, silent) to the OS's governed ``execute_query`` (ON — caps at
``row_cap`` and always ships ``truncated: true/false``, the #478 disclosure
lesson).

Metric = disclosure rate over OVER-CAP results: the fraction that carry a
truncation signal a consumer can act on. Structural, no LLM judge — the judge
version (does the agent actually say so) is Level-1's job. Uses the real
``SeochoOS.execute_query`` for ON.

Usage:
  python scripts/agentos/ablation_a5_honesty.py \
      --out outputs/agentos/ablation_a5_honesty.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


class _Store:
    """Fake governed store returning `n_rows` scoped rows."""

    def __init__(self, n_rows: int) -> None:
        self.rows = [{"n": {"id": i, "_workspace_id": "acme"}} for i in range(n_rows)]

    def query(self, cypher, *, params=None, database=None,
              enforce_workspace_filter=False):
        return list(self.rows)


def _off_raw(store, cypher) -> Dict[str, Any]:
    """A bare tool: hand the rows back as-is, no truncation metadata."""
    rows = store.query(cypher, params={"workspace_id": "acme"})
    return {"rows": rows, "row_count": len(rows)}   # note: no `truncated` key


def _has_disclosure(payload: Dict[str, Any]) -> bool:
    return "truncated" in payload


def run(cap: int, sizes: List[int]) -> Dict[str, Any]:
    from seocho.operating_layer import SeochoOS
    from seocho.ontology import NodeDef, Ontology, P

    onto = Ontology(name="a5", graph_model="lpg",
                    nodes={"N": NodeDef(properties={"id": P(int)})},
                    relationships={})
    results = []
    for size in sizes:
        store = _Store(size)
        os_layer = SeochoOS(ontology=onto, graph_store=store, database="neo4j",
                            workspace_id="acme", row_cap=cap)
        session = os_layer.session("s")
        over_cap = size > cap
        # ON: the governed path.
        on_payload = json.loads(os_layer.execute_query(
            session, "MATCH (n:N) WHERE n._workspace_id = $workspace_id RETURN n"))
        # OFF: the bare tool over the same rows.
        off_payload = _off_raw(store, "MATCH (n:N) RETURN n")
        results.append({
            "result_size": size, "over_cap": over_cap,
            "on_rows": on_payload["row_count"],
            "on_discloses": _has_disclosure(on_payload),
            "on_truncated_flag": on_payload.get("truncated"),
            "off_rows": off_payload["row_count"],
            "off_discloses": _has_disclosure(off_payload),
        })

    over = [r for r in results if r["over_cap"]]
    return {
        "row_cap": cap, "cases": results,
        "over_cap_cases": len(over),
        "on_disclosure_rate": round(
            sum(1 for r in over if r["on_discloses"]) / len(over), 3) if over else None,
        "off_disclosure_rate": round(
            sum(1 for r in over if r["off_discloses"]) / len(over), 3) if over else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cap", type=int, default=50)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    report = run(args.cap, sizes=[10, 40, 80, 200, 500])
    print("=== A5 truncation honesty: disclosure OFF vs ON ===")
    print(f"  row_cap = {args.cap}")
    print(f"  {'result_size':>11s} {'over_cap':>8s} {'ON rows':>8s} "
          f"{'ON discloses':>13s} {'OFF discloses':>14s}")
    for r in report["cases"]:
        print(f"  {r['result_size']:>11d} {str(r['over_cap']):>8s} "
              f"{r['on_rows']:>8d} {str(r['on_discloses'])+' ('+str(r['on_truncated_flag'])+')':>13s} "
              f"{str(r['off_discloses']):>14s}")
    print(f"\n  over-cap cases: {report['over_cap_cases']}")
    print(f"  ON  disclosure rate: {report['on_disclosure_rate']}  (signals truncation)")
    print(f"  OFF disclosure rate: {report['off_disclosure_rate']}  (silent — partial looks complete)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
