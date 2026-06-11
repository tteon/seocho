#!/usr/bin/env python3
"""Full-filing ingestion: SEC XBRL companyfacts -> backbone Observations (seocho-992).

seocho-eju's arms showed the MARA judge is uninformative because answers built
from gold-SNIPPET text don't carry the numbers FinDER's compositional gold
needs (revenue growth, EPS, operating margin). This loads the STRUCTURED source
of those numbers — SEC XBRL companyfacts (ADR-0103's deterministic ingester) —
onto the shared backbone, so a CompanyYear hub carries real Observations
(metric:Revenue FY2023 = 53,717,000,000 USD, ...).

For each SE_XCAT_HELDOUT company (seocho-88b): fetch_companyfacts(cik) ->
companyfacts_to_observations() -> load into a live DozerDB backbone as
(:Company)-[:HAS_OBSERVATION]->(:Observation) and (:CompanyYear)-[:REPORTS]->
(:Observation). Deterministic (no LLM); the only impure surface is the EDGAR
fetch. Reuses src/seocho/index/xbrl_ingest.py + semantic_layer.

This is the prerequisite step for the judge-informative arms layer; it does not
itself answer questions. xbrl_context(cik) renders the per-company numbers for
later arm integration.

Run: PYTHONPATH=src:scripts/benchmarks python3 scripts/benchmarks/finder_xbrl_backbone.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

from neo4j import GraphDatabase

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "src", _ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from finder_backbone import select_xcat_cases  # noqa: E402  (seocho-88b)
from seocho.index.xbrl_ingest import companyfacts_to_observations, fetch_companyfacts  # noqa: E402
from seocho.semantic_layer.concepts import default_registry  # noqa: E402
from seocho.semantic_layer.identity import EntityResolver  # noqa: E402

CONTAINER = "seocho-xbrl-neo4j"
PASSWORD = "seocho-dev"
IMAGE = "graphstack/dozerdb:5.26.3.0"
BOLT = "bolt://localhost:7695"
DB = "xbrlbackbone"
N_YEARS = 5
MIN_FY = 2021          # FinDER questions span FY2021-2024


def boot():
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", CONTAINER,
         "-e", f"NEO4J_AUTH=neo4j/{PASSWORD}", "-p", "7482:7474", "-p", "7695:7687", IMAGE],
        capture_output=True, text=True,
    )
    for _ in range(60):
        try:
            drv = GraphDatabase.driver(BOLT, auth=("neo4j", PASSWORD))
            with drv.session(database="system") as s:
                s.run("SHOW DATABASES YIELD name RETURN count(name) AS n").single()
                s.run(f"CREATE DATABASE `{DB}` IF NOT EXISTS").consume()
            time.sleep(3)
            return drv
        except Exception:
            time.sleep(2)
    return None


def _fy_from_period_key(period_key: str) -> int:
    # period_key looks like "fiscal:2023:FY"
    parts = period_key.split(":")
    for p in parts:
        if p.isdigit() and len(p) == 4:
            return int(p)
    return 0


def ingest_company(drv, cik: str, registry) -> int:
    """Fetch companyfacts for cik and load Observations onto the backbone."""
    try:
        facts = fetch_companyfacts(cik)
    except Exception as exc:  # network/availability
        print(f"   ! fetch failed for {cik}: {type(exc).__name__}")
        return 0
    nodes, _ = companyfacts_to_observations(
        facts, registry=registry, cik=cik, n_years=N_YEARS, min_fiscal_year=MIN_FY)
    obs = [n["properties"] for n in nodes if n["label"] == "Observation"]
    name = next((n["properties"].get("name") for n in nodes if n["label"] == "Company"), cik)
    rows = []
    for p in obs:
        fy = _fy_from_period_key(p["period_key"])
        rows.append({"cik": cik, "name": name, "fy": fy, "obs_id": p["obs_id"],
                     "concept": p["concept_id"], "val": p["value_num"], "unit": p["unit"]})
    with drv.session(database=DB) as s:
        s.run(
            "UNWIND $rows AS r "
            "MERGE (co:Company {cik:r.cik}) SET co.name=r.name "
            "MERGE (cy:CompanyYear {cy_id:'cy:'+r.cik+':'+toString(r.fy)}) "
            "  SET cy.cik=r.cik, cy.fy=r.fy "
            "MERGE (co)-[:FOR_YEAR]->(cy) "
            "MERGE (o:Observation {obs_id:r.obs_id}) "
            "  SET o.concept_id=r.concept, o.entity_cik=r.cik, o.fy=r.fy, "
            "      o.value_num=r.val, o.unit=r.unit "
            "MERGE (co)-[:HAS_OBSERVATION]->(o) "
            "MERGE (cy)-[:REPORTS]->(o)", rows=rows).consume()
    return len(obs)


def xbrl_context(drv, cik: str) -> str:
    """Render a company's structured Observations as answer context lines."""
    with drv.session(database=DB) as s:
        recs = s.run(
            "MATCH (co:Company {cik:$cik})-[:HAS_OBSERVATION]->(o:Observation) "
            "RETURN o.concept_id AS c, o.fy AS fy, o.value_num AS v, o.unit AS u "
            "ORDER BY c, fy", cik=cik).data()
    return "\n".join(f"{r['c']} FY{r['fy']} = {r['v']:.0f} {r['u']}" for r in recs)


def main() -> int:
    resolver = EntityResolver.from_frozen()
    if resolver is None:
        print("FATAL: frozen CIK table not found", file=sys.stderr)
        return 1
    cases = select_xcat_cases(resolver)
    ciks: List[str] = sorted({c.cik for c in cases})
    tk_by_cik: Dict[str, str] = {c.cik: c.ticker for c in cases}
    registry = default_registry()

    drv = boot()
    if drv is None:
        print("FATAL: throwaway DozerDB not ready", file=sys.stderr)
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        return 1
    try:
        print("=" * 80)
        print(f"SEC XBRL companyfacts -> backbone Observations ({len(ciks)} companies, "
              f"FY>={MIN_FY})")
        print("=" * 80)
        total = 0
        for cik in ciks:
            n = ingest_company(drv, cik, registry)
            total += n
            print(f"  {tk_by_cik[cik]:<6} cik={cik}  observations={n}")
            time.sleep(0.3)   # be gentle to EDGAR
        print(f"\n  TOTAL observations ingested: {total}")
        # sanity: show real numbers for one company (UAL revenue series)
        ual = resolver.resolve("UAL")
        print(f"\n  sample — UAL ({ual}) revenue/operating series:")
        for line in xbrl_context(drv, ual).splitlines():
            if "Revenue" in line or "OperatingIncome" in line:
                print(f"    {line}")
        print("\n  These structured numbers are what the compositional FinDER gold needs;")
        print("  next: feed xbrl_context into the backbone_multi arm so the MARA judge")
        print("  becomes informative (seocho-992 step 2).")
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
