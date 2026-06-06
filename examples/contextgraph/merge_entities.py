#!/usr/bin/env python3
"""$0 canonical entity-resolution merge (panel build B) — NO LLM.

The graph-strength work surfaced 14% of BC3 Person nodes fragmented ("Jacob" vs
"Jacob Palme", "Ian" vs "Ian J. Dickinson"), which splits a person's
proposals/stances across node variants and caps join/answer correctness. This
merges name-variant nodes into the fullest-name canonical node, in-place, per
workspace, via apoc.refactor.mergeNodes (discard=keep survivor props, merge+
dedupe rels). NO LLM. Read+write graph only.

Experiment 0 (2026-06-06) extended this to CONCEPT nodes: Proposal nodes are
fragmented worse than Person ('informal_sig_at_chi' [snake_case] + 'Informal SIG
at CHI 2003' + '...for W3C User interest group' = one proposal as 4 nodes =
anti-pattern #4 concept-instance/naming), splitting PROPOSES/SUPPORTS/OPPOSES
across variants → the deterministic answerer served a noisy near-dup list
(silent-wrong). Merging Proposals consolidates stance edges onto one node.
Default labels now: Person,Proposal (validated against a whitelist, §8).

"Before" metrics are recorded; after this, re-run failure_modes.py /
run_graph_answer_eval.py to get "after" and report the delta (§20: gain is a
hypothesis until re-judged). a1 is reproducible via run_e1 --build-only for a
clean pre-merge state.

Cluster rule: normalize (lowercase, strip punctuation); names A,B are the same
entity iff one token-SEQUENCE is an ordered prefix of the other (high precision —
'Alan' is NOT merged into 'Friend of Alan'); canonical = most tokens (tie→longest).

Run: python examples/contextgraph/merge_entities.py --db cgbc3minimaxm25 \
        --ws-prefix e1-bc3-a1-decision- --labels Person,Proposal
"""
from __future__ import annotations
import argparse, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
from seocho.store.graph import Neo4jGraphStore


# §8: dynamic labels must be validated against a whitelist before Cypher interpolation
_ALLOWED_LABELS = {"Person", "Proposal", "Topic", "Decision"}


def _norm_tokens(name):
    return re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()  # ORDERED list


def _is_prefix(a, b):
    """True if token-list a is an ordered prefix of b (a=['jacob'] prefix of
    ['jacob','palme']). Precise — avoids merging distinct names that merely share
    a token (e.g. 'Alan' is NOT a prefix of 'Friend of Alan')."""
    return len(a) <= len(b) and b[:len(a)] == a


def cluster(persons):
    """persons: list of (name, eid). Return (canonical_eid, [variant_eids], ...)."""
    toks = {eid: _norm_tokens(name) for name, eid in persons}
    names = {eid: name for name, eid in persons}
    eids = [eid for _, eid in persons]
    parent = {e: e for e in eids}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        parent[find(a)] = find(b)
    for i in range(len(eids)):
        for j in range(i + 1, len(eids)):
            a, b = eids[i], eids[j]
            ta, tb = toks[a], toks[b]
            # same entity iff one token-SEQUENCE is an ordered prefix of the other
            # (first-name → full-name, or exact duplicate). High precision.
            if ta and tb and (_is_prefix(ta, tb) or _is_prefix(tb, ta)):
                union(a, b)
    groups = {}
    for e in eids:
        groups.setdefault(find(e), []).append(e)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        canon = max(members, key=lambda e: (len(toks[e]), len(names[e])))
        variants = [m for m in members if m != canon]
        out.append((canon, variants, names[canon], [names[v] for v in variants]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="cgbc3minimaxm25")
    ap.add_argument("--ws-prefix", default="e1-bc3-a1-decision-")
    ap.add_argument("--labels", default="Person,Proposal",
                    help="comma-separated node labels to merge (whitelist-validated)")
    ap.add_argument("--dry-run", action="store_true", help="show clusters, do not merge")
    args = ap.parse_args()
    labels = [x.strip() for x in args.labels.split(",") if x.strip()]
    bad = [x for x in labels if x not in _ALLOWED_LABELS]
    if bad:
        raise SystemExit(f"refusing un-whitelisted label(s) {bad}; allowed={sorted(_ALLOWED_LABELS)} (§8)")
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    try:
        wss = [r["w"] for r in gs.query(
            "MATCH (n) WHERE n._workspace_id STARTS WITH $p RETURN DISTINCT n._workspace_id AS w ORDER BY w",
            params={"p": args.ws_prefix}, database=args.db)]
        per_label = {lab: 0 for lab in labels}
        for w in wss:
            for lab in labels:
                # lab is whitelist-validated above → safe to interpolate (§8)
                nodes = [(r["name"], r["eid"]) for r in gs.query(
                    f"MATCH (p:{lab} {{_workspace_id:$w}}) RETURN p.name AS name, elementId(p) AS eid",
                    params={"w": w}, database=args.db) if r["name"]]
                clusters = cluster(nodes)
                for canon_eid, variant_eids, canon_name, variant_names in clusters:
                    print(f"  [{lab} | {w.split('-')[-1]}] '{canon_name}' <= {variant_names}")
                    if args.dry_run:
                        continue
                    ordered = [canon_eid] + variant_eids  # canonical first = survivor
                    try:
                        gs.query(
                            "UNWIND range(0, size($eids)-1) AS i MATCH (n) WHERE elementId(n)=$eids[i] "
                            "WITH n, i ORDER BY i WITH collect(n) AS ns "
                            # 'discard' = survivor (canonical, ordered first) keeps its scalar
                            # props (esp. name); 'combine' would turn name into a list. mergeRels
                            # dedupes redirected edges (consolidates PROPOSES/SUPPORTS/OPPOSES).
                            "CALL apoc.refactor.mergeNodes(ns, {properties:'discard', mergeRels:true}) "
                            "YIELD node RETURN elementId(node)",
                            params={"eids": ordered}, database=args.db)
                        per_label[lab] += len(variant_eids)
                    except Exception as e:
                        print(f"    merge err: {type(e).__name__}: {str(e)[:80]}")
        tag = "DRY-RUN — " if args.dry_run else ""
        summary = ", ".join(f"{lab}: {per_label[lab]}" for lab in labels)
        print(f"\n{tag}merged variant nodes across {len(wss)} workspaces ({args.ws_prefix}) — {summary}")
    finally:
        gs.close()


if __name__ == "__main__":
    main()
