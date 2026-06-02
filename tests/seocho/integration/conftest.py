"""Shared fixtures for GOPTS F4 live PROFILE oracle integration tests.

Skips the test session entirely when DozerDB is unreachable so CI
without Docker stays green. Operator runs::

    docker compose up -d neo4j
    NEO4J_PASSWORD=... pytest -m integration_gopts tests/seocho/integration/

The session fixture loads the FIBO-lite corpus once per pytest session
and tears it down at the end so repeated runs against a shared instance
don't accumulate state.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import pytest


WORKSPACE_ID = "fixture-gopts"
DATABASE = os.environ.get("SEOCHO_GOPTS_DATABASE", "neo4j")


def _connect_or_skip() -> Any:
    """Open a Neo4j driver against the env-configured DozerDB.

    Skips the test session at fixture-setup time when:
      - the ``neo4j`` Python package isn't installed
      - NEO4J_PASSWORD isn't set
      - the bolt URI isn't reachable
      - authentication fails

    Operator messages tell the runner exactly what to do (set the env
    var, start the container) so the skip isn't a silent dead end.
    """
    try:
        from neo4j import GraphDatabase
    except ImportError:
        pytest.skip("integration_gopts requires the 'neo4j' package")

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        pytest.skip(
            "integration_gopts requires NEO4J_PASSWORD; "
            "set it in .env and re-run, or `docker compose up -d neo4j`."
        )

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=DATABASE) as session:
            session.run("RETURN 1").consume()
    except Exception as exc:
        driver.close()
        pytest.skip(f"integration_gopts: DozerDB unreachable at {uri}: {exc}")
    return driver


@pytest.fixture(scope="session")
def gopts_live_driver() -> Iterator[Any]:
    """Session-scoped DozerDB driver with the FIBO-lite corpus pre-loaded.

    Idempotent: the loader uses MERGE so a stale workspace from a prior
    crashed session is healed, not duplicated.
    """
    from scripts.eval.load_gopts_fibo_corpus import load, teardown

    driver = _connect_or_skip()
    try:
        load(driver, database=DATABASE, workspace_id=WORKSPACE_ID)
        yield driver
        try:
            teardown(driver, database=DATABASE, workspace_id=WORKSPACE_ID)
        except Exception:
            # Best-effort cleanup. A teardown failure shouldn't fail the
            # test session — operator can run --teardown manually later.
            pass
    finally:
        driver.close()
