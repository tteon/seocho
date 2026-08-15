"""Ablation: ontology-drift barrier OFF (today) vs ON (seocho-ia4.1).

Measures whether stamping the ontology version in GraphProjector.project() and
wiring enforce_drift_policy on the read path actually converts SEOCHO's
"detect-and-warn" into a real barrier — and whether it stays quiet on fresh data.

This exercises the REAL code path end to end, deterministically (no live DB, no
RNG): the real GraphProjector, the real build_ontology_context_summary_query via
query_ontology_context_mismatch, the real assess_ontology_context_mismatch +
enforce_drift_policy. Only the graph store is an in-memory fake that records the
projected node properties and answers the summary query from them — so the ONLY
difference between arms is whether the projector stamped the version (the fix).

Arms:
- OFF  = today's behavior: project() without an ontology context -> nodes carry
         no _ontology_context_hash -> the summary query reads empty -> drift is
         invisible (the GraphProjector stamping bug).
- ON   = the fix: project(..., ontology_context=v1) -> nodes stamped -> drift is
         detected and enforce_drift_policy(policy='block') blocks it.

Scenarios (worst / best / mixed), each: data written under ontology v1, then
queried under some active version.
- breaking_bump (WORST): active = v2 (breaking change) -> real drift on 100% data.
- no_bump       (BEST / NULL control): active = v1 -> no drift; barrier must NOT fire.
- mixed         : half the data written under v2 -> active = v2 -> partial drift.

Headline: the barrier flips drift DETECTION from ~0% (blind) to ~100% on real
drift, while the null control stays at 0% false-positives (no fresh-data tax).

Usage: python scripts/agentos/ablation_drift_barrier.py --out outputs/agentos/drift_barrier.json
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, List

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from seocho.ontology.core import Ontology  # noqa: E402
from seocho.ontology.context import (  # noqa: E402
    compile_ontology_context,
    enforce_drift_policy,
    query_ontology_context_mismatch,
)
from seocho.graph_projector import GraphProjector  # noqa: E402
from seocho.qualification import (  # noqa: E402
    CanonicalEntityRecord,
    GraphProjectionSnapshot,
)

_WS = "acme"
_DB = "neo4j"
_N = 40  # entities per scenario


class FakeGraphStore:
    """In-memory store: records projected node props; answers the ontology
    summary query from them (the aggregation the real Cypher computes)."""

    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = []

    def write(self, nodes, relationships, *, database, workspace_id, source_id=None):
        for n in nodes:
            self.nodes.append(dict(n.get("properties", {})))
        return {"nodes_created": len(nodes), "relationships_created": len(relationships)}

    def query(self, cypher, params=None, database=None):
        ws = (params or {}).get("workspace_id", "default")
        scoped = [
            n for n in self.nodes
            if str(n.get("_workspace_id", n.get("workspace_id", ws))) == str(ws)
        ]
        hashes = sorted({str(n.get("_ontology_context_hash", "")) for n in scoped})
        missing = sum(1 for n in scoped if not str(n.get("_ontology_context_hash", "")))
        # mirror build_ontology_context_summary_query's projection keys
        return [{
            "raw_context_hashes": hashes,
            "scoped_nodes": len(scoped),
            "missing_context_nodes": missing,
        }]


def _make_ontologies():
    """v1 = the example schema; v2 = a breaking change (drop one node label) so
    the compiled context_hash differs."""
    yamls = list(Path("examples").rglob("schema.yaml")) or list(Path("examples").rglob("*.yaml"))
    o1 = Ontology.load(str(yamls[0]))
    o2 = Ontology.load(str(yamls[0]))
    # breaking change: remove a node type -> different schema -> different context_hash
    if getattr(o2, "nodes", None):
        drop = sorted(o2.nodes.keys())[0]
        del o2.nodes[drop]
    o2.version = "2.0.0"
    c1 = compile_ontology_context(o1, workspace_id=_WS)
    c2 = compile_ontology_context(o2, workspace_id=_WS)
    assert c1.descriptor.context_hash != c2.descriptor.context_hash, "need distinct context hashes"
    return (o1, c1), (o2, c2)


def _snapshot(n: int, tag: str) -> GraphProjectionSnapshot:
    ents = [
        CanonicalEntityRecord(
            entity_id=f"{tag}-{i}", entity_type="Company",
            canonical_name=f"{tag}-co-{i}", properties={}, support_count=1,
        )
        for i in range(n)
    ]
    return GraphProjectionSnapshot(
        snapshot_id=f"snap-{tag}", workspace_id=_WS, graph_id=_DB, database=_DB, entities=ents,
    )


def _run_arm(*, stamp: bool, active_ctx, write_ctx_by_half=None, write_ctx=None) -> Dict[str, Any]:
    """Project data (optionally stamped) then assess+enforce drift under active_ctx.

    Returns detection (mismatch caught) and blocked flags. `stamp=False` is the
    OFF arm (projector gets no context -> today's bug)."""
    store = FakeGraphStore()
    projector = GraphProjector(graph_store=store, workspace_id=_WS)
    if write_ctx_by_half is not None:
        # mixed: first half under v1, second half under v2
        c_a, c_b = write_ctx_by_half
        projector.project(_snapshot(_N // 2, "a"), database=_DB,
                          ontology_context=(c_a if stamp else None))
        projector.project(_snapshot(_N - _N // 2, "b"), database=_DB,
                          ontology_context=(c_b if stamp else None))
    else:
        projector.project(_snapshot(_N, "x"), database=_DB,
                          ontology_context=(write_ctx if stamp else None))

    assessment = query_ontology_context_mismatch(
        store, active_ctx, workspace_id=_WS, database=_DB,
    )
    enforced = enforce_drift_policy(copy.deepcopy(assessment), policy="block")
    return {
        "detected_mismatch": bool(assessment.get("mismatch")),
        "blocked": bool(enforced.get("blocked")),
        "indexed_hashes": assessment.get("indexed_context_hashes", []),
        "scoped_nodes": assessment.get("scoped_nodes", 0),
        "missing_context_nodes": assessment.get("missing_context_nodes", 0),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    (o1, c1), (o2, c2) = _make_ontologies()

    scenarios = {
        # name: (active_ctx, write kwargs, is_real_drift)
        "breaking_bump_WORST": dict(active_ctx=c2, write_ctx=c1, real_drift=True),
        "no_bump_BEST_null": dict(active_ctx=c1, write_ctx=c1, real_drift=False),
        "mixed": dict(active_ctx=c2, write_ctx_by_half=(c1, c2), real_drift=True),
    }

    report: Dict[str, Any] = {"entities_per_scenario": _N,
                              "v1_hash": c1.descriptor.context_hash,
                              "v2_hash": c2.descriptor.context_hash,
                              "scenarios": {}}
    for name, spec in scenarios.items():
        real_drift = spec.pop("real_drift")
        off = _run_arm(stamp=False, **spec)
        on = _run_arm(stamp=True, **spec)
        report["scenarios"][name] = {
            "real_drift": real_drift,
            "OFF_today": off,
            "ON_fixed": on,
        }

    # aggregate: detection on real-drift scenarios; false-positive on the null
    real = [v for v in report["scenarios"].values() if v["real_drift"]]
    null = [v for v in report["scenarios"].values() if not v["real_drift"]]
    report["summary"] = {
        "OFF_detection_rate_on_real_drift": round(
            sum(1 for v in real if v["OFF_today"]["detected_mismatch"]) / len(real), 3),
        "ON_detection_rate_on_real_drift": round(
            sum(1 for v in real if v["ON_fixed"]["detected_mismatch"]) / len(real), 3),
        "OFF_false_positive_on_fresh": round(
            sum(1 for v in null if v["OFF_today"]["detected_mismatch"]) / max(len(null), 1), 3),
        "ON_false_positive_on_fresh": round(
            sum(1 for v in null if v["ON_fixed"]["detected_mismatch"]) / max(len(null), 1), 3),
    }

    s = report["summary"]
    print("=== ontology-drift barrier ablation (seocho-ia4.1) ===")
    print(f"  {'':22s} {'OFF (today)':>14s} {'ON (fixed)':>12s}")
    print(f"  {'detection on drift':22s} {s['OFF_detection_rate_on_real_drift']:>13.0%} "
          f"{s['ON_detection_rate_on_real_drift']:>12.0%}")
    print(f"  {'false-pos on fresh':22s} {s['OFF_false_positive_on_fresh']:>13.0%} "
          f"{s['ON_false_positive_on_fresh']:>12.0%}  (null control)")
    for name, v in report["scenarios"].items():
        print(f"  - {name:24s} real_drift={v['real_drift']}  "
              f"OFF caught={v['OFF_today']['detected_mismatch']}  "
              f"ON caught={v['ON_fixed']['detected_mismatch']} blocked={v['ON_fixed']['blocked']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
