#!/usr/bin/env python3
"""Teardown: drop the rebuildable staging DB + composite (if created).

NEVER touches the department DBs (paid extraction output) or ``mdmmaster``
(the demo's product). Both survive; ``mdmstaging`` is fully reproducible
from 03_federate_and_stage.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
sys.path.insert(0, str(MDM_ROOT))
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

from dotenv import dotenv_values  # noqa: E402

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ.setdefault(k, v)

from lib import federation  # noqa: E402


def main() -> int:
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
                               auth=(os.environ.get("NEO4J_USER", "neo4j"),
                                     os.environ.get("NEO4J_PASSWORD", "")))
    try:
        with drv.session(database="system") as s:
            s.run("DROP DATABASE mdmstaging IF EXISTS").consume()
            print("dropped mdmstaging (rebuildable via 03_federate_and_stage.py)")
        try:
            federation.drop_composite(drv, composite="mdmcomp",
                                      aliases={"risk": "mdmrisk",
                                               "research": "mdmresearch",
                                               "compliance": "mdmcompliance"})
            print("dropped composite mdmcomp + aliases (if they existed)")
        except Exception as exc:
            # Expected on fanout mode (composite admin commands unsupported).
            print(f"composite teardown skipped: {type(exc).__name__}")
        print("kept: mdmrisk / mdmresearch / mdmcompliance (paid) and mdmmaster (product)")
        return 0
    finally:
        drv.close()


if __name__ == "__main__":
    raise SystemExit(main())
