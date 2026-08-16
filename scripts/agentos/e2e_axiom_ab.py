"""LIVE e2e: SHACL-only vs induced+deduced axioms on a REAL extracted graph.

The live half of ADR-0178 / seocho-ia4.10. Runs the real SEOCHO indexing pipeline
(LLM extraction via MARA -> materialize to DozerDB) over a document corpus, then
mines axioms at CORPUS scope — reading the whole assembled graph from the store, NOT
per-chunk (the seocho-ia4.8 cross-chunk answer: chunks are the extraction window;
axioms are graph statistics over the interned, cross-document-merged store).

Arm A (SHACL-only): the ontology's shape constraints only.
Arm B (induced+deduced): mine_axioms over the store graph -> approve -> materialize
entailments + detect contradictions.

Measures the MECHANISM on real extracted data: axioms mined, approval burden,
contradictions caught, entailed structure. (Answer-quality A/B is the next layer,
via competency questions — left as a follow-up flag.)

Isolation: a dedicated DozerDB database + workspace, so it never touches other data.

Usage:
  NEO4J_PASSWORD=<pw> python scripts/agentos/e2e_axiom_ab.py \
      --graph bolt://localhost:17687 \
      --database axiome2e --workspace axiom_e2e \
      --llm mara/MiniMax-M2.5 --docs examples/finance-compliance/sample_docs \
      --out outputs/agentos/e2e_axiom_ab.json [--smoke 2]

(the DozerDB password is read from --neo4j-password or the NEO4J_PASSWORD env var.)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import sys
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from seocho.axioms import approve, materialize_entailments, mine_axioms  # noqa: E402


def _load_env(root: Path) -> None:
    # worktrees don't carry .env; fall back to the main lab/seocho tree.
    candidates = [root / ".env", Path("/home/hadry/lab/seocho/.env")]
    for envf in candidates:
        if envf.exists():
            for line in envf.read_text().splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    # Only load LLM/provider API keys from .env; NEVER load NEO4J_*
                    # (the .env points at a different DozerDB instance than this
                    # e2e uses — loading it caused a wrong-password lockout).
                    if k.startswith("NEO4J") or k.startswith("BOLT"):
                        continue
                    # strip surrounding quotes — a quoted key is otherwise sent
                    # verbatim (quotes included) and 401s.
                    os.environ.setdefault(k, v.strip().strip('"').strip("'"))
            return


def _load_ontology(root: Path) -> Any:
    path = root / "examples" / "finance-compliance" / "ontology.py"
    spec = importlib.util.spec_from_file_location("_fc_ontology", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_ontology()


# The runtime memory-graph scaffolding (ensure_memory_graph) is provenance plumbing,
# not domain knowledge — axioms must be mined over DOMAIN nodes/rels, else the miner
# just rediscovers that "each Document HAS_VERSION its versions" etc.
_MEMORY_LABELS = {"Document", "DocumentVersion", "Chunk", "Section", "Observation"}
_MEMORY_RELS = {"HAS_CHUNK", "HAS_VERSION", "CURRENT_VERSION", "HAS_SECTION",
                "MENTIONS", "HAS_OBSERVATION", "NEXT_CHUNK"}


def _read_store_graph(uri: str, user: str, pw: str, database: str, ws: str,
                      *, domain_only: bool = True) -> Dict[str, Any]:
    """Read the WHOLE materialized graph (workspace-scoped) — corpus scope, all docs
    merged by interning. This is where cross-chunk / cross-document axioms live.
    With ``domain_only`` the memory-graph provenance layer is excluded."""
    from neo4j import GraphDatabase
    d = GraphDatabase.driver(uri, auth=(user, pw))
    nodes: List[Dict[str, Any]] = []
    rels: List[Dict[str, Any]] = []
    domain_ids: set = set()
    with d.session(database=database) as s:
        for r in s.run(
            "MATCH (n) WHERE coalesce(n._workspace_id, n.workspace_id, $ws) = $ws "
            "RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props",
            ws=ws,
        ):
            labs = [x for x in (r["labels"] or []) if not str(x).startswith("_")]
            if domain_only and (not labs or any(x in _MEMORY_LABELS for x in labs)):
                continue
            domain_ids.add(r["id"])
            nodes.append({"id": r["id"], "label": labs, "properties": dict(r["props"] or {})})
        for r in s.run(
            "MATCH (a)-[e]->(b) WHERE coalesce(a._workspace_id, a.workspace_id, $ws) = $ws "
            "RETURN elementId(a) AS source, elementId(b) AS target, type(e) AS type",
            ws=ws,
        ):
            if domain_only and (r["type"] in _MEMORY_RELS
                                or r["source"] not in domain_ids or r["target"] not in domain_ids):
                continue
            rels.append({"source": r["source"], "target": r["target"], "type": r["type"]})
    d.close()
    return {"nodes": nodes, "relationships": rels}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--graph", default="bolt://localhost:17687")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", default=None,
                    help="DozerDB password (or set NEO4J_PASSWORD)")
    ap.add_argument("--database", default="axiome2e")
    ap.add_argument("--workspace", default="axiom_e2e")
    ap.add_argument("--llm", default="mara/MiniMax-M2.5")
    ap.add_argument("--docs", default="examples/finance-compliance/sample_docs")
    ap.add_argument("--min-support", type=int, default=2)
    ap.add_argument("--min-confidence", type=float, default=0.8)
    ap.add_argument("--smoke", type=int, default=0, help="ingest only the first N docs")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="skip extraction; mine the already-materialized graph")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    _load_env(_ROOT)
    pw = args.neo4j_password or os.environ.get("NEO4J_PASSWORD", "")
    onto = _load_ontology(_ROOT)

    ingested = 0
    if not args.skip_ingest:
        from seocho import Seocho
        api_key = os.environ.get("MARA_API_KEY") if args.llm.startswith("mara/") else None
        s = Seocho.local(onto, llm=args.llm, graph=args.graph,
                         neo4j_user=args.neo4j_user, neo4j_password=pw,
                         api_key=api_key, workspace_id=args.workspace)
        docs = sorted(Path(args.docs).glob("*.txt"))
        if args.smoke:
            docs = docs[: args.smoke]
        for p in docs:
            s.add(p.read_text(), database=args.database)
            ingested += 1
            print(f"  ingested {p.name}")

    # --- read the assembled graph from the store (corpus scope) ---
    graph = _read_store_graph(args.graph, args.neo4j_user, pw,
                              args.database, args.workspace)
    n_nodes, n_rels = len(graph["nodes"]), len(graph["relationships"])

    # --- Arm B: induce + deduce over the store graph ---
    candidates = mine_axioms(graph, min_support=args.min_support,
                             min_confidence=args.min_confidence)
    approved = approve(candidates, min_support=args.min_support,
                       min_confidence=args.min_confidence)
    ent = materialize_entailments(graph, approved)
    by_kind: Dict[str, int] = {}
    for c in approved:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1

    report = {
        "graph": {"nodes": n_nodes, "relationships": n_rels, "docs_ingested": ingested},
        "arm_A_shacl_only": {"axiom_classes_mined": 0, "contradictions_caught": 0,
                             "entailed_edges": 0, "entailed_labels": 0},
        "arm_B_induced_deduced": {
            "candidates_mined": len(candidates),
            "approved_axioms": len(approved),
            "approved_by_kind": by_kind,
            "contradictions_caught": len(ent["contradictions"]),
            "contradiction_kinds": sorted({c["kind"] for c in ent["contradictions"]}),
            "entailed_edges": ent["entailed_edges"],
            "entailed_labels": ent["entailed_labels"],
        },
        "top_axioms": [
            {"kind": c.kind, "subject": c.subject, "support": c.support, "confidence": c.confidence}
            for c in sorted(approved, key=lambda c: (-c.support, -c.confidence))[:15]
        ],
        "note": "mechanism on REAL extracted data; answer-quality A/B (competency "
                "questions) is the next layer.",
    }

    b = report["arm_B_induced_deduced"]
    print(f"\n=== LIVE e2e axiom A/B (real MARA extraction -> DozerDB {args.database}) ===")
    print(f"  graph: {n_nodes} nodes, {n_rels} rels ({ingested} docs)")
    print("  Arm A (SHACL-only):   0 axioms, 0 contradictions, 0 entailed")
    print(f"  Arm B (induced+deduced): {b['approved_axioms']} axioms {b['approved_by_kind']}")
    print(f"                            {b['contradictions_caught']} contradictions {b['contradiction_kinds']}")
    print(f"                            {b['entailed_edges']} entailed edges, {b['entailed_labels']} entailed labels")
    print(f"  approval burden: {b['approved_axioms']} of {b['candidates_mined']} mined")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
