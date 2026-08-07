#!/usr/bin/env python3
"""Read-only retrieval outcomes for the 256-cell orthogonal mediation gate."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
MANIFEST = BASE / "log2026-observation-policy-v1/experiment_manifest.json"
INDEX = BASE / "fedcat-full-matrix-v1/index_partial"
OUT = BASE / "log2026-factorial-mediation-v1/retrieval.json"


def main() -> int:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    import yaml
    load_dotenv(ROOT / ".env")
    manifest = json.loads(MANIFEST.read_text()); cells = manifest["cells"]
    records = [json.loads(path.read_text()) for path in INDEX.glob("*.json")]
    lookup = {(r["case_id"], r["model"], r["scenario_id"]): r for r in records}
    # The completed factorial lives in the preserved four-provider snapshot;
    # active single-DBMS config points at a newer runtime and must not be used.
    config = {
        "deepseek": {"uri": "bolt://localhost:7797", "database": "mdmdeepseek"},
        "gptoss": {"uri": "bolt://localhost:7797", "database": "mdmgptoss"},
        "minimax25": {"uri": "bolt://localhost:7797", "database": "mdmminimax25"},
        "minimax27": {"uri": "bolt://localhost:7797", "database": "mdmminimax27"},
    }
    index_spec = importlib.util.spec_from_file_location("finder", ROOT / "examples/mdm/11_index_providers.py"); assert index_spec and index_spec.loader
    finder = importlib.util.module_from_spec(index_spec); index_spec.loader.exec_module(finder)
    cases = {row["case_id"]: row for row in finder.load_cases_full(42)}
    rank_spec = importlib.util.spec_from_file_location("rank", ROOT / "examples/mdm/41_sdcr_development_retrieval.py"); assert rank_spec and rank_spec.loader
    rank = importlib.util.module_from_spec(rank_spec); rank_spec.loader.exec_module(rank)
    auth = (os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]); rows = []
    by_provider = {}
    for provider, spec in config.items(): by_provider[provider] = GraphDatabase.driver(spec["uri"], auth=auth)
    try:
        for cell in cells:
            scenario = cell["prompt_id"].split("@")[0].replace("_", "-") + "-v1__" + cell["ontology_id"].replace("_", "-")
            # Existing records are authoritative for exact scenario spelling.
            candidates = [r for r in records if r["case_id"] == cell["case_id"] and r["model"] == cell["model"]
                          and r["scenario_id"].startswith(cell["prompt_id"].split("@")[0]) and r["scenario_id"].endswith(cell["ontology_id"])]
            if len(candidates) != 1: raise RuntimeError(f"cell lookup {cell['cell_id']} matched {len(candidates)}")
            record = candidates[0]; provider = record["provider_id"]; spec = config[provider]
            with by_provider[provider].session(database=spec["database"]) as session:
                nodes = session.run("MATCH (n {_workspace_id:$w}) RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props", w=record["workspace_id"]).data()
                triples = session.run("MATCH (a {_workspace_id:$w})-[r]->(b {_workspace_id:$w}) RETURN elementId(a) AS source, type(r) AS type, elementId(b) AS target", w=record["workspace_id"]).data()
            question = cases[cell["case_id"]]["query"]; gold = cases[cell["case_id"]]["expected_answer"]
            ordered, method = rank.personalized_rank({"nodes": nodes, "triples": triples}, question); evidence = ordered[:20]
            cov = rank.coverage(evidence, gold)
            rows.append({**cell, "scenario_id": record["scenario_id"], "workspace_id": record["workspace_id"],
                         "question": question, "gold": gold, "nodes_created": record["nodes_created"], "rels_created": record["rels_created"],
                         "retrieval_method": method, "retrieved_nodes": evidence, "token_recall": cov["token_recall"], "number_recall": cov["number_recall"]})
    finally:
        for driver in by_provider.values(): driver.close()
    if not rows or any(not row["retrieved_nodes"] for row in rows):
        raise RuntimeError("one or more factorial graph workspaces are empty; refusing to emit mediation artifact")
    summary = {"cells": len(rows), "mean_token_recall": mean(r["token_recall"] for r in rows),
               "mean_number_recall": mean(r["number_recall"] for r in rows if r["number_recall"] is not None),
               "new_extractions": 0, "database_access": "read-only"}
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps({"contract": "log2026.factorial_retrieval_mediation.v1", "summary": summary, "rows": rows}, indent=2) + "\n")
    print(json.dumps(summary, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
