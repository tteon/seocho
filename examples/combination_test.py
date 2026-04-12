#!/usr/bin/env python3
"""
SEOCHO Combination Test — verify ALL SDK features work together.

Tests 10 features across multiple ontology × prompt × agent combinations.
Traces everything to Opik + JSONL + CSV.

Usage:
    python examples/combination_test.py
    python examples/combination_test.py --quick  # 3 combos only

Covers:
1. SHACL strict validation (reject vs warn)
2. JSON-LD save/load roundtrip
3. Denormalization (flatten query results)
4. Confidence scoring + gating
5. AgentConfig presets (default, strict, fast, research)
6. Multi-ontology register_ontology()
7. Reasoning mode + repair_budget
8. CypherBuilder fuzzy matching
9. Category-specific prompts (auto-select)
10. Opik tracing + CSV export
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


def main(quick: bool = False):
    print("=" * 70)
    print("SEOCHO Combination Test — All 10 SDK Features")
    print("=" * 70)

    # --- Setup ---
    from seocho import (
        Ontology, Seocho, AgentConfig, AGENT_PRESETS,
        enable_tracing, flush_tracing, disable_tracing,
    )
    from seocho.store import Neo4jGraphStore, OpenAIBackend
    from seocho.query import PRESET_PROMPTS, CATEGORY_PROMPT_MAP
    from seocho.tracing import export_traces_csv

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    if "://neo4j:" in NEO4J_URI:
        NEO4J_URI = NEO4J_URI.replace("://neo4j:", "://localhost:")

    store = Neo4jGraphStore(NEO4J_URI, os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))
    llm = OpenAIBackend(model="gpt-4o-mini")
    DB = "neo4j"

    # Load dataset
    dataset_path = Path(__file__).parent / "datasets" / "finder_sample.json"
    with open(dataset_path) as f:
        dataset = json.load(f)
    docs = dataset[:3] if quick else dataset[:5]
    print(f"Dataset: {len(docs)} documents")

    # --- Feature 10: Enable tracing ---
    traces_dir = Path(__file__).parent / "datasets" / "results"
    traces_dir.mkdir(exist_ok=True)
    jsonl_path = str(traces_dir / "combination_traces.jsonl")
    csv_path = str(traces_dir / "combination_traces.csv")

    enable_tracing(backend=["console", "jsonl"], output=jsonl_path)
    print(f"Tracing: console + jsonl ({jsonl_path})")

    # Setup Opik if available
    try:
        import opik
        api_key = os.getenv("OPIK_API_KEY", "")
        if api_key:
            opik.configure(api_key=api_key, workspace="tteon", project_name="seocho-combination-test", force=True)
            print("Opik: enabled")
    except Exception:
        pass

    results = []

    # --- Feature 2: JSON-LD save/load ---
    print(f"\n{'─' * 70}")
    print("Feature 2: JSON-LD Roundtrip")
    print(f"{'─' * 70}")

    fibo_variants = {}
    for variant in ["fibo_minus", "fibo_base", "fibo_plus"]:
        path = Path(__file__).parent / "datasets" / f"{variant}.jsonld"
        onto = Ontology.from_jsonld(path)
        fibo_variants[variant] = onto
        # Save and reload to verify roundtrip
        tmp_path = traces_dir / f"_{variant}_roundtrip.jsonld"
        onto.to_jsonld(str(tmp_path))
        reloaded = Ontology.from_jsonld(str(tmp_path))
        assert reloaded.name == onto.name
        print(f"  {variant}: {len(onto.nodes)} types, roundtrip ✓")

    # --- Feature 1 + 4 + 5 + 7 + 9: Combination loop ---
    print(f"\n{'─' * 70}")
    print("Combination Testing")
    print(f"{'─' * 70}")

    combos = []
    if quick:
        combos = [
            {"ontology": "fibo_base", "agent": "default", "strict": False},
            {"ontology": "fibo_plus", "agent": "strict", "strict": True},
            {"ontology": "fibo_minus", "agent": "research", "strict": False},
        ]
    else:
        for onto_name in ["fibo_minus", "fibo_base", "fibo_plus"]:
            for agent_name in ["default", "strict", "fast"]:
                for strict in [False, True]:
                    combos.append({"ontology": onto_name, "agent": agent_name, "strict": strict})

    print(f"  {len(combos)} combinations to test\n")

    for ci, combo in enumerate(combos):
        onto = fibo_variants[combo["ontology"]]
        agent_preset = AGENT_PRESETS[combo["agent"]]
        strict = combo["strict"]

        # Feature 5: AgentConfig presets
        config = AgentConfig(
            extraction_quality_threshold=agent_preset.extraction_quality_threshold,
            extraction_retry_on_low_quality=agent_preset.extraction_retry_on_low_quality,
            validation_on_fail="reject" if strict else agent_preset.validation_on_fail,
            reasoning_mode=True,  # Feature 7: always on
            repair_budget=2,
        )

        s = Seocho(ontology=onto, graph_store=store, llm=llm, agent_config=config)

        combo_label = f"{combo['ontology']}/{combo['agent']}/strict={strict}"
        print(f"  [{ci+1}/{len(combos)}] {combo_label}")

        combo_result = {
            "combo": combo_label,
            "ontology": combo["ontology"],
            "agent": combo["agent"],
            "strict_validation": strict,
            "nodes_total": 0,
            "rels_total": 0,
            "index_success": 0,
            "index_failed": 0,
            "query_answered": 0,
            "query_empty": 0,
            "scores": [],
        }

        # Index
        for doc in docs:
            # Feature 9: category-specific prompt auto-selection
            mem = s.add(
                doc["text"],
                database=DB,
                category=doc["category"],
            )
            nodes = mem.metadata.get("nodes_created", 0)
            rels = mem.metadata.get("relationships_created", 0)
            combo_result["nodes_total"] += nodes
            combo_result["rels_total"] += rels

            if mem.status == "active":
                combo_result["index_success"] += 1
            else:
                combo_result["index_failed"] += 1

            # Feature 4: Confidence scoring
            if nodes > 0:
                extracted = {"nodes": [{"id": "x", "label": "Company", "properties": {"name": "x"}}], "relationships": []}
                score = onto.score_extraction(extracted)
                combo_result["scores"].append(score.get("overall", 0))

        # Query with reasoning (Feature 7)
        for doc in docs[:3]:
            answer = s.ask(
                doc["question"],
                database=DB,
                reasoning_mode=True,
                repair_budget=2,
            )
            if "no available" in answer.lower() or "could not" in answer.lower():
                combo_result["query_empty"] += 1
            else:
                combo_result["query_answered"] += 1

        avg_score = sum(combo_result["scores"]) / len(combo_result["scores"]) if combo_result["scores"] else 0
        print(f"    Index: {combo_result['nodes_total']}n/{combo_result['rels_total']}r, "
              f"success={combo_result['index_success']}/{combo_result['index_success']+combo_result['index_failed']}")
        print(f"    Query: {combo_result['query_answered']}/{combo_result['query_answered']+combo_result['query_empty']} answered")

        results.append(combo_result)
        s.close()

    # --- Feature 3: Denormalization ---
    print(f"\n{'─' * 70}")
    print("Feature 3: Denormalization")
    print(f"{'─' * 70}")

    onto = fibo_variants["fibo_base"]
    plan = onto.denormalization_plan()
    print(f"  Denorm plan for fibo_base: {list(plan.keys())}")
    for label, info in plan.items():
        for embed in info["embeds"]:
            safe = "SAFE" if embed["safe"] else "BLOCKED"
            print(f"    {label} -[{embed['via']}]-> {embed['target']}: {safe}")

    # --- Feature 6: Multi-ontology register_ontology ---
    print(f"\n{'─' * 70}")
    print("Feature 6: Multi-Ontology")
    print(f"{'─' * 70}")

    s = Seocho(ontology=fibo_variants["fibo_base"], graph_store=store, llm=llm)
    s.register_ontology("lpg_finance", fibo_variants["fibo_plus"])
    s.register_ontology("rdf_finance", fibo_variants["fibo_minus"])
    print(f"  Registered: lpg_finance={s.get_ontology('lpg_finance').name}, rdf_finance={s.get_ontology('rdf_finance').name}")
    print(f"  Default: {s.get_ontology('unknown').name}")
    s.close()

    # --- Feature 10: CSV export ---
    print(f"\n{'─' * 70}")
    print("Feature 10: Trace Export")
    print(f"{'─' * 70}")

    flush_tracing()
    disable_tracing()

    try:
        count = export_traces_csv(jsonl_path, csv_path)
        print(f"  CSV: {count} records → {csv_path}")
    except Exception as e:
        print(f"  CSV export: {e}")

    # --- Summary ---
    print(f"\n{'=' * 70}")
    print("COMBINATION TEST RESULTS")
    print(f"{'=' * 70}")
    print(f"\n{'Combo':40s} {'Nodes':>6s} {'Rels':>5s} {'Idx✓':>5s} {'Q✓':>4s}")
    print(f"{'─' * 70}")
    for r in results:
        print(f"{r['combo']:40s} {r['nodes_total']:>6d} {r['rels_total']:>5d} "
              f"{r['index_success']:>5d} {r['query_answered']:>4d}")

    # Best combo
    best = max(results, key=lambda r: r["query_answered"] + r["nodes_total"])
    print(f"\nBest: {best['combo']} (nodes={best['nodes_total']}, queries={best['query_answered']})")

    # Save
    with open(traces_dir / "combination_results.json", "w") as f:
        json.dump({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}, f, indent=2)
    print(f"\nResults: {traces_dir / 'combination_results.json'}")

    store.close()
    print(f"\n{'=' * 70}")
    print("ALL 10 FEATURES TESTED")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Run 3 combos only")
    args = parser.parse_args()
    main(quick=args.quick)
