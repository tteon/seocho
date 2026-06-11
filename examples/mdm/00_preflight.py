#!/usr/bin/env python3
"""MDM demo preflight — environment checks + the composite-vs-fanout decision.

Checks (modeled on examples/teaching/_shared/preflight.py):

  [Neo4j]      DozerDB reachable, kernel/edition reported
  [GDS]        gds.version() present; legacy cypher projection + wcc available
  [Composite]  CREATE COMPOSITE DATABASE smoke test → decides federation mode
  [MARA]       MARA_API_KEY present (the extraction step is the only paid one)
  [Embedding]  local sentence-transformers + BGE model importable ($0 tier)

The composite verdict is persisted to ``examples/mdm/outputs/mode.json`` and
every later pipeline step branches on it — DozerDB 5.26 is *expected* to lack
composite (fabric is a v2.0+ roadmap item), in which case mode=fanout and the
demo proceeds on client-side federation. Exit code: 0 OK/WARN, 2 on FAIL.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
REPO_ROOT = MDM_ROOT.parents[1]
sys.path.insert(0, str(MDM_ROOT))
sys.path.insert(0, str(REPO_ROOT))

from dotenv import dotenv_values  # noqa: E402

for k, v in dotenv_values(REPO_ROOT / ".env").items():
    if v is not None:
        os.environ.setdefault(k, v)

from lib import federation  # noqa: E402

# Reuse the teaching preflight's result model + renderer (same UX).
sys.path.insert(0, str(REPO_ROOT / "examples" / "teaching"))
from _shared.preflight import CheckResult, render  # noqa: E402


def _driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "")),
    )


def check_neo4j(driver) -> CheckResult:
    try:
        with driver.session() as s:
            comp = s.run("CALL dbms.components() YIELD name, versions, edition "
                         "RETURN name, versions[0] AS v, edition").data()
        row = comp[0]
        return CheckResult("Neo4j", "OK", f"{row['name']} {row['v']} ({row['edition']})")
    except Exception as exc:
        return CheckResult("Neo4j", "FAIL", f"{type(exc).__name__}: {str(exc)[:100]}",
                           hint="docker compose up -d neo4j; check NEO4J_URI/NEO4J_PASSWORD")


def check_gds(driver) -> CheckResult:
    try:
        with driver.session() as s:
            ver = s.run("RETURN gds.version() AS v").data()[0]["v"]
            procs = {r["name"] for r in s.run(
                "SHOW PROCEDURES YIELD name WHERE name IN "
                "['gds.graph.project.cypher', 'gds.wcc.stream', 'gds.nodeSimilarity.stream'] "
                "RETURN name").data()}
        missing = {"gds.graph.project.cypher", "gds.wcc.stream",
                   "gds.nodeSimilarity.stream"} - procs
        if missing:
            return CheckResult("GDS", "FAIL", f"v{ver}, missing {sorted(missing)}",
                               hint="OpenGDS build mismatch — re-run 01_install_gds.sh")
        return CheckResult("GDS", "OK", f"OpenGDS {ver} (cypher projection + wcc + nodeSimilarity)")
    except Exception as exc:
        return CheckResult("GDS", "FAIL", f"{type(exc).__name__}: {str(exc)[:100]}",
                           hint="bash examples/mdm/01_install_gds.sh (installs OpenGDS + allowlist)")


def check_composite(driver) -> CheckResult:
    ok, msg = federation.composite_smoke_test(driver)
    mode = "composite" if ok else "fanout"
    path = federation.write_mode(mode, {"smoke_result": msg})
    if ok:
        return CheckResult("Composite", "OK", f"supported — mode=composite ({path.name})")
    # Expected on DozerDB 5.26: not a failure, the fan-out path is primary.
    return CheckResult("Composite", "WARN", f"unsupported — mode=fanout ({msg})",
                       hint="Expected on DozerDB 5.26 (fabric = v2.0+ roadmap); "
                            "the demo federates client-side instead.")


def check_mara() -> CheckResult:
    if os.environ.get("MARA_API_KEY"):
        return CheckResult("MARA", "OK", "MARA_API_KEY set (DeepSeek-V3.1 / gpt-oss-120b / MiniMax-M2.5)")
    return CheckResult("MARA", "FAIL", "MARA_API_KEY unset",
                       hint="02_extract_departments.py needs the MARA gateway")


def check_embedding() -> CheckResult:
    try:
        import sentence_transformers  # noqa: F401
        return CheckResult("Embedding", "OK",
                           f"sentence-transformers {sentence_transformers.__version__} "
                           "(local BGE tier, $0)")
    except ImportError:
        return CheckResult("Embedding", "WARN", "sentence-transformers not installed",
                           hint="pip install sentence-transformers — the embedding match "
                                "tier will be skipped (recorded, not silent)")


def main() -> int:
    driver = None
    try:
        driver = _driver()
        results = [check_neo4j(driver)]
        if results[0].status == "FAIL":
            results += [CheckResult("GDS", "FAIL", "skipped (Neo4j down)"),
                        CheckResult("Composite", "FAIL", "skipped (Neo4j down)")]
        else:
            results += [check_gds(driver), check_composite(driver)]
    finally:
        if driver is not None:
            driver.close()
    results += [check_mara(), check_embedding()]
    print(render(results))
    if any(r.status == "FAIL" for r in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
