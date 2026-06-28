#!/usr/bin/env python3
"""FinDER common-backbone vs per-category-isolated — first vertical slice.

Implements seocho-303 (backbone graph build) + seocho-88b (cross-category case
selector) for the FinDER common-backbone agent experiments (epic seocho-vet),
designed with GraphDBMS + LLM-engineer review.

FinDER's 3 categories (Financials / Company overview / Footnotes) are three
VIEWS of one company 10-K. The common substrate is Company(CIK) x FiscalYear x
FilingSection. This harness builds the SHARED backbone (one Company node per
CIK, all categories' evidence hung off it via a CompanyYear hub) vs the
PER-CATEGORY-ISOLATED graph (today's per-_case_id islands) on a LIVE throwaway
DozerDB, and measures deterministic Tier-1 metrics that need no LLM:

  - entity_identity_consistency: fraction of CIKs represented by exactly 1
      Company node (backbone = 1.0; isolated < 1.0 — one duplicate per case)
  - avg_company_nodes_per_cik (backbone = 1.0; isolated = #cases/company)
  - cross_category_reachability: distinct FilingSection kinds reachable from a
      SINGLE company anchor (backbone reaches all the company's categories;
      isolated reaches 1 — each case is its own island)

Scope: deterministic structure only (CIK from the frozen resolver, fiscal years
parsed from the query, gold snippets as Evidence). The Observation/metric layer
+ LLM answering are later tickets (x42/qog/8zh/8mr). Claim: the backbone enables
cross-category joins that per-category isolation cannot — NOT that it improves
recall. Tenant/company isolation is orthogonal.

Run: PYTHONPATH=src python3 scripts/benchmarks/finder_backbone.py
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from neo4j import GraphDatabase

from seocho.semantic_layer.identity import EntityResolver

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT.parent / "examples/datasets/finder/all_slices.csv"
SLICE_OUT = ROOT.parent / "examples/datasets/finder/se_xcat_heldout.csv"

CONTAINER = "seocho-finderbb-neo4j"
PASSWORD = "seocho-dev"
IMAGE = "graphstack/dozerdb:5.26.3.0"
BOLT = "bolt://localhost:7692"
DB = "finderbackbone"

REF_SEP = "===EVIDENCE_BOUNDARY==="
XCAT_TICKERS = ["AON", "BBWI", "CE", "CNC", "RMD", "SYK", "SYY", "UAL", "VRTX"]


# --------------------------------------------------------------------------
# seocho-88b: cross-category case selector
# --------------------------------------------------------------------------
@dataclass
class Case:
    case_id: str
    ticker: str
    cik: str
    category: str
    rtype: str
    years: List[int]
    query: str
    evidence: List[str]


def _years(text: str) -> List[int]:
    ys = {int(t) for t in re.findall(r"\b(20\d\d)\b", text)}
    ys |= {2000 + int(t) for t in re.findall(r"\bFY(\d\d)\b", text)}
    return sorted(ys)


def _split_refs(joined: str) -> List[str]:
    parts = joined.split(REF_SEP) if REF_SEP in joined else [joined]
    return [p.strip() for p in parts if p.strip()]


def select_xcat_cases(resolver: EntityResolver) -> List[Case]:
    """seocho-88b: SE_XCAT_HELDOUT — rows for the 9 cross-category tickers,
    ticker resolved to CIK, years parsed. Deterministic, no LLM."""
    cases: List[Case] = []
    with open(DATASET, newline="") as fh:
        for row in csv.DictReader(fh):
            q = row["query"]
            hit = next((t for t in XCAT_TICKERS if re.search(rf"\b{t}\b", q)), None)
            if not hit:
                continue
            cik = resolver.resolve(hit)
            if not cik:
                continue
            cases.append(
                Case(
                    case_id=row["_id"],
                    ticker=hit,
                    cik=cik,
                    category=row["category"],
                    rtype=row["type"],
                    years=_years(q) or _years(row["references_joined"]),
                    query=q,
                    evidence=_split_refs(row["references_joined"]),
                )
            )
    return cases


def write_slice(cases: List[Case]) -> None:
    with open(SLICE_OUT, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["slice", "case_id", "ticker", "cik", "category", "type", "years", "query"]
        )
        for c in cases:
            w.writerow(
                [
                    "SE_XCAT_HELDOUT",
                    c.case_id,
                    c.ticker,
                    c.cik,
                    c.category,
                    c.rtype,
                    "|".join(map(str, c.years)),
                    c.query,
                ]
            )


# --------------------------------------------------------------------------
# Live DozerDB (neo4j driver — batched, fast)
# --------------------------------------------------------------------------
def boot() -> Optional["GraphDatabase.driver"]:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-e",
            f"NEO4J_AUTH=neo4j/{PASSWORD}",
            "-p",
            "7479:7474",
            "-p",
            "7692:7687",
            IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    for _ in range(60):
        try:
            drv = GraphDatabase.driver(BOLT, auth=("neo4j", PASSWORD))
            with drv.session(database="system") as s:
                # system DB forbids `RETURN 1`; use a system-valid readiness probe
                s.run("SHOW DATABASES YIELD name RETURN count(name) AS n").single()
                s.run(f"CREATE DATABASE `{DB}` IF NOT EXISTS").consume()
            for _ in range(20):  # wait for the new DB to come online
                try:
                    with drv.session(database=DB) as s:
                        s.run("RETURN 1").single()
                    return drv
                except Exception:
                    time.sleep(1)
            return drv
        except Exception:
            time.sleep(2)
    return None


def _rows(cases: List[Case]) -> List[dict]:
    return [
        {
            "cik": c.cik,
            "ticker": c.ticker,
            "cat": c.category,
            "years": c.years or [0],
            "case": c.case_id,
            "ev": [e[:400] for e in c.evidence],
        }
        for c in cases
    ]


# --------------------------------------------------------------------------
# seocho-303: build BACKBONE vs ISOLATED (UNWIND batch)
# --------------------------------------------------------------------------
def build_backbone(drv, cases: List[Case]) -> None:
    """Shared backbone: ONE Company per CIK; CompanyYear hub; all categories'
    sections + evidence hang off it — cross-category structure materialized."""
    with drv.session(database=DB) as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
        s.run(
            "UNWIND $rows AS r "
            "MERGE (co:Company {cik:r.cik}) SET co.ticker=r.ticker "
            "WITH r, co UNWIND r.years AS fy "
            "MERGE (cy:CompanyYear {cy_id:'cy:'+r.cik+':'+toString(fy)}) "
            "  SET cy.cik=r.cik, cy.fy=fy "
            "MERGE (co)-[:FOR_YEAR]->(cy) "
            "MERGE (fs:FilingSection {fs_id:cy.cy_id+':'+r.cat}) SET fs.kind=r.cat "
            "MERGE (cy)-[:HAS_SECTION]->(fs)",
            rows=_rows(cases),
        ).consume()
        s.run(
            "UNWIND $rows AS r "
            "WITH r, 'cy:'+r.cik+':'+toString(coalesce(r.years[0],0))+':'+r.cat AS fsid "
            "MATCH (fs:FilingSection {fs_id:fsid}) "
            "UNWIND range(0,size(r.ev)-1) AS i "
            "MERGE (e:Evidence {ev_id:r.case+':'+toString(i)}) "
            "  SET e.category=r.cat, e.cik=r.cik, e.text=r.ev[i] "
            "MERGE (fs)-[:CONTAINS]->(e)",
            rows=_rows(cases),
        ).consume()


def build_isolated(drv, cases: List[Case]) -> None:
    """Per-category-isolated: each case is its own island with its OWN Company
    duplicate (keyed by case_id) — mirrors today's per-_case_id loading."""
    with drv.session(database=DB) as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
        s.run(
            "UNWIND $rows AS r "
            "MERGE (co:Company {cik:r.cik, _case_id:r.case}) SET co.ticker=r.ticker "
            "MERGE (fs:FilingSection {fs_id:r.case+':'+r.cat}) SET fs.kind=r.cat "
            "MERGE (co)-[:HAS_SECTION]->(fs) "
            "WITH r, fs UNWIND range(0,size(r.ev)-1) AS i "
            "MERGE (e:Evidence {ev_id:r.case+':'+toString(i)}) "
            "  SET e.category=r.cat, e.cik=r.cik, e.text=r.ev[i] "
            "MERGE (fs)-[:CONTAINS]->(e)",
            rows=_rows(cases),
        ).consume()


# --------------------------------------------------------------------------
# seocho-8zh (core): deterministic Tier-1 metrics
# --------------------------------------------------------------------------
def metrics(drv, cases: List[Case], mode: str) -> Dict[str, float]:
    ciks = sorted({c.cik for c in cases})
    cat_by_cik: Dict[str, set] = {}
    for c in cases:
        cat_by_cik.setdefault(c.cik, set()).add(c.category)
    multi = [cik for cik in ciks if len(cat_by_cik[cik]) >= 2]
    with drv.session(database=DB) as s:
        nodes = {
            r["cik"]: r["n"]
            for r in s.run(
                "MATCH (co:Company) RETURN co.cik AS cik, count(*) AS n"
            ).data()
        }
        # reachable categories from a SINGLE company anchor
        if mode == "backbone":
            reach = {
                r["cik"]: r["k"]
                for r in s.run(
                    "MATCH (co:Company)-[:FOR_YEAR]->(:CompanyYear)-[:HAS_SECTION]->(fs) "
                    "RETURN co.cik AS cik, count(DISTINCT fs.kind) AS k"
                ).data()
            }
        else:
            reach = {
                r["cik"]: r["k"]
                for r in s.run(
                    "MATCH (co:Company)-[:HAS_SECTION]->(fs) "
                    "WITH co, count(DISTINCT fs.kind) AS k "
                    "RETURN co.cik AS cik, max(k) AS k"
                ).data()
            }
    identity = sum(1 for cik in ciks if nodes.get(cik, 0) == 1) / len(ciks)
    avg_nodes = sum(nodes.get(cik, 0) for cik in ciks) / len(ciks)
    xcat_success = sum(1 for cik in multi if reach.get(cik, 0) >= 2) / len(multi)
    avg_reach = sum(reach.get(cik, 0) for cik in ciks) / len(ciks)
    return {
        "entity_identity_consistency": identity,
        "avg_company_nodes_per_cik": avg_nodes,
        "cross_category_reachability_success": xcat_success,
        "avg_reachable_categories": avg_reach,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--select-only", action="store_true", help="write the slice CSV and exit"
    )
    args = ap.parse_args()

    resolver = EntityResolver.from_frozen()
    if resolver is None:
        print("FATAL: frozen CIK table not found", file=sys.stderr)
        return 1
    cases = select_xcat_cases(resolver)
    write_slice(cases)
    print(
        f"seocho-88b: SE_XCAT_HELDOUT = {len(cases)} cases / "
        f"{len({c.cik for c in cases})} companies / "
        f"{len({c.category for c in cases})} categories -> {SLICE_OUT}"
    )
    if args.select_only:
        return 0

    drv = boot()
    if drv is None:
        print("FATAL: throwaway DozerDB not ready", file=sys.stderr)
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        return 1
    try:
        print("\n" + "=" * 84)
        print(
            "FinDER backbone vs isolated — Tier-1 deterministic metrics (LIVE DozerDB)"
        )
        print("=" * 84)
        rows = {}
        for mode, builder in (
            ("isolated", build_isolated),
            ("backbone", build_backbone),
        ):
            builder(drv, cases)
            rows[mode] = metrics(drv, cases, mode)
        keys = [
            "entity_identity_consistency",
            "avg_company_nodes_per_cik",
            "cross_category_reachability_success",
            "avg_reachable_categories",
        ]
        print(f"\n  {'metric':<38} {'ISOLATED':>10} {'BACKBONE':>10}")
        print("  " + "-" * 60)
        for k in keys:
            print(
                f"  {k:<38} {rows['isolated'][k]:>10.2f} {rows['backbone'][k]:>10.2f}"
            )
        print("\n  Reading: backbone = 1 Company node/CIK reaching ALL its categories;")
        print("  isolated duplicates the company per case and reaches only 1 category.")
        print(
            "  => cross-category joins are possible on the backbone, impossible when isolated."
        )
    finally:
        try:
            with drv.session(database="system") as s:
                s.run(f"DROP DATABASE `{DB}` IF EXISTS").consume()
            drv.close()
        except Exception:
            pass
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        print("\nthrowaway DozerDB removed; running stack untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
