"""Freshness refusal-ROC with REAL signals from the ia4.2 compatibility classifier.

Promotes ablation_freshness_roc.py (which hand-assigned relevance/distance) to use
signals DERIVED from a real ontology version diff via classify_ontology_change
(seocho-ia4.2). The comparison is non-tautological: ground truth is at
PROPERTY/value granularity (does the query read a property whose change actually
invalidates the answer), while the classifier signals are at coarser granularities,
so the frontier is set by SIGNAL FIDELITY, not by construction.

Policies (decision: serve vs refuse), over a real v0 -> v_active ontology diff:
- always_warn  : serve everything (under-refuses).
- always_block : the ia4.1 binary barrier — any global mismatch -> refuse all.
- fresh_OLD    : diff_ontologies' false-major signal — refuse any query touching a
                 CHANGED-or-removed label (marks compatible changes breaking too).
- fresh_label  : ia4.2 breaking_labels — refuse only queries touching a label with
                 an answer-invalidating change (compatible-only changes served).
- fresh_prop   : ia4.2 breaking_properties (+ structural) — refuse only queries that
                 READ an invalidating property.

Ground truth (property level): a query is invalid-if-served iff it reads an
invalidating (label, property) or touches a structurally-broken label (removed
node / retyped-or-tightened relationship). Metrics: under-refusal (served-but-
invalid / invalid), over-refusal (refused-but-valid / valid).

Expected ladder: freshness beats both fixed corners, and over-refusal shrinks
monotonically as the classifier signal sharpens (OLD -> label -> property) at 0
under-refusal. Fully-live (real data + real answers on DozerDB) is the e2e run.

Usage: python scripts/agentos/ablation_freshness_live.py --out outputs/agentos/freshness_live.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from seocho.ontology.compatibility import classify_ontology_change  # noqa: E402

_COPIES = 25  # per query pattern -> stable rates


def _versions():
    """A real v0 and an evolved v_active with a realistic mix of change atoms."""
    v0 = {
        "version": "1.0.0",
        "nodes": {
            "Company": {"properties": {"name": {"type": "STRING", "constraint": "UNIQUE"},
                                       "hq": {"type": "STRING"}}},
            "Team": {"properties": {"name": {"type": "STRING"}}},
            "Project": {"properties": {"name": {"type": "STRING"},
                                       "budget": {"type": "STRING"}}},
            "Person": {"properties": {"name": {"type": "STRING"},
                                      "alias": {"type": "STRING"}}},
            "Region": {"properties": {"name": {"type": "STRING"}}},
        },
        "relationships": {
            "MEMBER_OF": {"source": "Person", "target": "Team", "cardinality": "MANY_TO_MANY"},
        },
    }
    v_active = json.loads(json.dumps(v0))
    v_active["version"] = "2.0.0"
    v_active["nodes"]["Company"]["properties"]["owner_id"] = {"type": "STRING", "constraint": "REQUIRED"}  # BREAKING
    v_active["nodes"]["Team"]["properties"]["slug"] = {"type": "STRING"}                                   # BACKWARD
    v_active["nodes"]["Project"]["properties"]["budget"] = {"type": "INTEGER"}                             # BREAKING (retype)
    del v_active["nodes"]["Person"]["properties"]["alias"]                                                 # BREAKING (removed prop)
    v_active["nodes"]["Vendor"] = {"properties": {"name": {"type": "STRING"}}}                             # BACKWARD (node add)
    v_active["relationships"]["MEMBER_OF"]["cardinality"] = "ONE_TO_MANY"                                  # BREAKING (tighten)
    return v0, v_active


def _query_patterns():
    """(label, prop, invalid_if_served). prop='' = a structural touch (e.g. a rel)."""
    return [
        ("Company", "name", False),      # breaking label, unbroken prop -> valid
        ("Company", "owner_id", True),   # reads the new required prop -> invalid
        ("Team", "name", False),         # compatible-changed label -> valid
        ("Team", "slug", False),         # reading a new optional prop over old data -> valid
        ("Project", "budget", True),     # retyped prop -> invalid
        ("Project", "name", False),      # unbroken -> valid
        ("Person", "alias", True),       # removed prop -> invalid
        ("Person", "name", False),       # unbroken -> valid
        ("Region", "name", False),       # unchanged label -> valid
        ("MEMBER_OF", "", True),         # tightened relationship -> invalid
    ]


def _old_breaking_labels(v0: Dict[str, Any], vA: Dict[str, Any]) -> Set[str]:
    """diff_ontologies' notion: any changed-or-removed node/rel is 'breaking'
    (the false-major bug — compatible changes included)."""
    out: Set[str] = set()
    for coll in ("nodes", "relationships"):
        o, n = v0.get(coll, {}), vA.get(coll, {})
        out |= set(o) - set(n)                                   # removed
        out |= {k for k in set(o) & set(n)
                if json.dumps(o[k], sort_keys=True) != json.dumps(n[k], sort_keys=True)}  # changed
    return out


def _decide(policy: str, label: str, prop: str, *, changed: bool,
            old_breaking: Set[str], breaking_labels: Set[str],
            breaking_props: Set[Tuple[str, str]], structural: Set[str]) -> bool:
    """True = serve, False = refuse."""
    if policy == "always_warn":
        return True
    if policy == "always_block":
        return not changed          # refuse everything under a changed ontology
    if policy == "fresh_OLD":
        return label not in old_breaking
    if policy == "fresh_label":
        return label not in breaking_labels
    if policy == "fresh_prop":
        return not ((label, prop) in breaking_props or label in structural)
    raise ValueError(policy)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    v0, vA = _versions()
    report = classify_ontology_change(v0, vA)                       # REAL ia4.2 signal
    breaking_labels = report.breaking_labels
    breaking_props = report.breaking_properties
    # structural = invalidating atoms with no property (node/rel-level)
    structural = {a.label for a in report.atoms if a.invalidating and not a.prop}
    old_breaking = _old_breaking_labels(v0, vA)
    changed_globally = report.overall != "NONE"

    pop: List[Dict[str, Any]] = []
    for (label, prop, invalid) in _query_patterns():
        for _ in range(_COPIES):
            pop.append({"label": label, "prop": prop, "invalid": invalid})

    policies = ["always_warn", "always_block", "fresh_OLD", "fresh_label", "fresh_prop"]
    results: Dict[str, Dict[str, float]] = {}
    invalid_pop = [q for q in pop if q["invalid"]]
    valid_pop = [q for q in pop if not q["invalid"]]
    for pol in policies:
        served_invalid = sum(1 for q in invalid_pop if _decide(
            pol, q["label"], q["prop"], changed=changed_globally, old_breaking=old_breaking,
            breaking_labels=breaking_labels, breaking_props=breaking_props, structural=structural))
        refused_valid = sum(1 for q in valid_pop if not _decide(
            pol, q["label"], q["prop"], changed=changed_globally, old_breaking=old_breaking,
            breaking_labels=breaking_labels, breaking_props=breaking_props, structural=structural))
        results[pol] = {
            "under_refusal": round(served_invalid / max(len(invalid_pop), 1), 3),
            "over_refusal": round(refused_valid / max(len(valid_pop), 1), 3),
        }

    out_report = {
        "ontology_change": {
            "overall": report.overall,
            "breaking_labels": sorted(breaking_labels),
            "breaking_properties": sorted(f"{a}.{b}" for a, b in breaking_props),
            "old_false_major_breaking_labels": sorted(old_breaking),
            "atoms": [f"{a.kind}:{a.label}.{a.prop}={a.compatibility}" for a in report.atoms],
        },
        "population": len(pop),
        "policies": results,
    }

    print("=== freshness refusal-ROC, LIVE signals from ia4.2 classifier (seocho-ia4.6) ===")
    print(f"  real diff overall={report.overall}  "
          f"NEW breaking_labels={sorted(breaking_labels)}  "
          f"OLD(false-major) breaking_labels={sorted(old_breaking)}")
    print(f"  {'policy':16s} {'under_refusal':>14s} {'over_refusal':>13s}")
    for pol in policies:
        r = results[pol]
        tag = ""
        if pol == "fresh_OLD":
            tag = "  <- diff_ontologies false-major"
        elif pol == "fresh_prop":
            tag = "  <- ia4.2 property-level: dominates"
        print(f"  {pol:16s} {r['under_refusal']:>14.0%} {r['over_refusal']:>13.0%}{tag}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(out_report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
