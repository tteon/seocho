#!/usr/bin/env python3
"""
SEOCHO E2E Evaluation with FinDER Dataset + Opik Tracing

This script:
1. Loads FinDER sample data or the gated Hugging Face FinDER dataset
2. Indexes into separate LPG and RDF databases (Neo4j naming convention)
3. Queries both and compares answers
4. Evaluates quality via Opik experiment

Usage:
    python examples/e2e_evaluation.py
    python examples/e2e_evaluation.py --dataset-source hf --split 'train[:5]'

Requires:
    - Neo4j/DozerDB running on bolt://localhost:7687
    - OPENAI_API_KEY in .env
    - OPIK_API_KEY in .env (optional, for Opik cloud)
    - HF_TOKEN in .env when using `--dataset-source hf`

Neo4j database naming convention:
    - lowercase only, no hyphens, no underscores
    - e.g. finderlpg, finderrdf, seochoe2elpg
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Use default database to avoid Neo4j routing/DNS issues
# LPG and RDF data are separated by node labels, not databases
LPG_DATABASE = "neo4j"
RDF_DATABASE = "neo4j"
# Allow host-side fallback when .env uses the Docker-internal `neo4j` hostname.
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPIK_PROJECT = os.getenv("OPIK_PROJECT_NAME", "seocho-opik-test")
OPIK_WORKSPACE = os.getenv("OPIK_WORKSPACE", "tteon").strip('"')
DATASET_PATH = Path(__file__).parent / "datasets" / "finder_sample.json"
HF_DATASET_ID = "Linq-AI-Research/FinDER"
HF_SPLIT = "train[:10]"


def _resolve_host_neo4j_uri(uri: str) -> str:
    """Convert Docker-internal neo4j hostnames to localhost for host-side runs."""
    parsed = urlparse(uri)
    if parsed.hostname != "neo4j":
        return uri

    fallback_port = parsed.port or int(os.getenv("NEO4J_BOLT_PORT", "7687"))
    rewritten = parsed._replace(netloc=f"localhost:{fallback_port}")
    resolved = urlunparse(rewritten)
    print(f"Neo4j URI rewrite: {uri} -> {resolved}")
    return resolved


def _load_dataset(dataset_source: str, split: str):
    if dataset_source == "sample":
        print(f"\nLoading dataset: {DATASET_PATH}")
        with open(DATASET_PATH) as f:
            dataset = json.load(f)
        print(f"  {len(dataset)} sample documents loaded")
        return dataset

    if dataset_source == "hf":
        from datasets import load_dataset

        hf_token = os.getenv("HF_TOKEN", "").strip()
        if hf_token and not os.getenv("HUGGINGFACE_HUB_TOKEN"):
            os.environ["HUGGINGFACE_HUB_TOKEN"] = hf_token

        print(f"\nLoading Hugging Face dataset: {HF_DATASET_ID} ({split})")
        dataset = load_dataset(HF_DATASET_ID, split=split)
        rows = [dict(row) for row in dataset]
        print(f"  {len(rows)} HF documents loaded")
        return rows

    raise ValueError(f"Unsupported dataset source: {dataset_source}")


def _normalize_record(record: dict) -> dict:
    """Unify the local sample and gated HF FinDER formats."""
    if "question" in record:
        return {
            "id": record["id"],
            "context_text": record["text"],
            "question": record["question"],
            "expected_answer": record.get("expected_answer", ""),
            "category": record["category"],
            "raw_reasoning_type": record.get("reasoning_type"),
        }

    references = record.get("references", [])
    if isinstance(references, list):
        context_text = "\n".join(str(item) for item in references)
    else:
        context_text = str(references)

    return {
        "id": record.get("_id", "unknown"),
        "context_text": context_text,
        "question": record["text"],
        "expected_answer": record.get("answer", ""),
        "category": record["category"],
        "raw_reasoning_type": record.get("type") or record.get("reasoning"),
    }


def main(dataset_source: str = "sample", split: str = HF_SPLIT):
    print("=" * 70)
    print("SEOCHO E2E Evaluation — FinDER Dataset")
    print("=" * 70)

    # --- Setup Opik ---
    opik_enabled = False
    try:
        import opik
        api_key = os.getenv("OPIK_API_KEY", "")
        if api_key:
            opik.configure(
                api_key=api_key,
                workspace=OPIK_WORKSPACE,
                force=True,
            )
            opik_enabled = True
            print(f"Opik: enabled (workspace={OPIK_WORKSPACE}, project={OPIK_PROJECT})")
    except Exception as exc:
        print(f"Opik: disabled ({exc})")

    # --- Load dataset ---
    dataset = _load_dataset(dataset_source=dataset_source, split=split)
    normalized_dataset = [_normalize_record(item) for item in dataset]

    # --- Setup SDK ---
    from seocho import Ontology, NodeDef, RelDef, P, Seocho
    from seocho.store import Neo4jGraphStore, OpenAIBackend
    from seocho.query import PRESET_PROMPTS

    # LPG ontology
    lpg_ontology = Ontology(
        name="finder_lpg",
        graph_model="lpg",
        nodes={
            "Company": NodeDef(description="A business entity", properties={
                "name": P(str, unique=True), "sector": P(str), "headquarters": P(str),
            }),
            "Person": NodeDef(description="An executive or individual", properties={
                "name": P(str, unique=True), "title": P(str),
            }),
            "FinancialMetric": NodeDef(description="A financial figure", properties={
                "name": P(str, unique=True), "value": P(str), "year": P(str),
            }),
            "Risk": NodeDef(description="A risk factor", properties={
                "name": P(str, unique=True), "category": P(str),
            }),
            "LegalIssue": NodeDef(description="A legal proceeding", properties={
                "name": P(str, unique=True), "status": P(str),
            }),
        },
        relationships={
            "REPORTED": RelDef(source="Company", target="FinancialMetric", description="Company reported metric"),
            "EMPLOYS": RelDef(source="Company", target="Person", description="Employment"),
            "FACES": RelDef(source="Company", target="Risk", description="Risk exposure"),
            "INVOLVED_IN": RelDef(source="Company", target="LegalIssue", description="Legal involvement"),
        },
    )

    # RDF ontology (same entities, but RDF mode)
    rdf_ontology = Ontology(
        name="finder_rdf",
        graph_model="rdf",
        namespace="https://seocho.dev/fibo/",
        nodes={
            "Company": NodeDef(same_as="schema:Organization", description="A business entity", properties={
                "uri": P(str, unique=True), "name": P(str), "sector": P(str),
            }),
            "Person": NodeDef(same_as="schema:Person", description="An executive", properties={
                "uri": P(str, unique=True), "name": P(str), "title": P(str),
            }),
            "FinancialMetric": NodeDef(description="A financial figure", properties={
                "uri": P(str, unique=True), "name": P(str), "value": P(str),
            }),
        },
        relationships={
            "reported": RelDef(source="Company", target="FinancialMetric", same_as="fibo:hasReportedMetric"),
            "employs": RelDef(source="Company", target="Person", same_as="schema:employee"),
        },
    )

    resolved_neo4j_uri = _resolve_host_neo4j_uri(NEO4J_URI)
    store = Neo4jGraphStore(resolved_neo4j_uri, NEO4J_USER, NEO4J_PASSWORD)
    llm = OpenAIBackend(model=MODEL)

    # Verify databases exist
    _ensure_database(store, LPG_DATABASE)
    _ensure_database(store, RDF_DATABASE)
    print(f"  Using: {LPG_DATABASE} (LPG) + {RDF_DATABASE} (RDF)")

    lpg_client = Seocho(ontology=lpg_ontology, graph_store=store, llm=llm,
                        workspace_id=f"finder_lpg_{int(time.time())}",
                        extraction_prompt=PRESET_PROMPTS["finder_financials"])
    rdf_client = Seocho(ontology=rdf_ontology, graph_store=store, llm=llm,
                        workspace_id=f"finder_rdf_{int(time.time())}",
                        extraction_prompt=PRESET_PROMPTS["finder_financials_rdf"])

    print(f"  LPG workspace: {lpg_client.workspace_id}")
    print(f"  RDF workspace: {rdf_client.workspace_id}")

    # --- Phase 1: Indexing ---
    print(f"\n{'─' * 70}")
    print("Phase 1: Indexing")
    print(f"{'─' * 70}")

    lpg_results = []
    rdf_results = []

    for i, doc in enumerate(normalized_dataset):
        print(f"\n  [{i+1}/{len(normalized_dataset)}] {doc['id']}: {doc['question'][:60]}...")

        # LPG indexing
        if opik_enabled:
            @opik.track(name="e2e.index.lpg", project_name=OPIK_PROJECT)
            def _index_lpg(text):
                return lpg_client.add(text, database=LPG_DATABASE, category=doc["category"])
            mem_lpg = _index_lpg(doc["context_text"])
        else:
            mem_lpg = lpg_client.add(doc["context_text"], database=LPG_DATABASE, category=doc["category"])

        lpg_results.append({
            "id": doc["id"],
            "nodes": mem_lpg.metadata.get("nodes_created", 0),
            "rels": mem_lpg.metadata.get("relationships_created", 0),
            "status": mem_lpg.status,
        })
        print(f"    LPG: {lpg_results[-1]['nodes']} nodes, {lpg_results[-1]['rels']} rels")

        # RDF indexing
        if opik_enabled:
            @opik.track(name="e2e.index.rdf", project_name=OPIK_PROJECT)
            def _index_rdf(text):
                return rdf_client.add(text, database=RDF_DATABASE, category=doc["category"])
            mem_rdf = _index_rdf(doc["context_text"])
        else:
            mem_rdf = rdf_client.add(doc["context_text"], database=RDF_DATABASE, category=doc["category"])

        rdf_results.append({
            "id": doc["id"],
            "nodes": mem_rdf.metadata.get("nodes_created", 0),
            "rels": mem_rdf.metadata.get("relationships_created", 0),
            "status": mem_rdf.status,
        })
        print(f"    RDF: {rdf_results[-1]['nodes']} nodes, {rdf_results[-1]['rels']} rels")

    # --- Phase 2: Querying ---
    print(f"\n{'─' * 70}")
    print("Phase 2: Querying")
    print(f"{'─' * 70}")

    query_results = []

    for doc in normalized_dataset:
        q = doc["question"]
        print(f"\n  Q: {q}")

        if opik_enabled:
            @opik.track(name="e2e.query.lpg", project_name=OPIK_PROJECT)
            def _query_lpg(question):
                return lpg_client.ask(question, database=LPG_DATABASE, reasoning_mode=True, repair_budget=1)
            lpg_answer = _query_lpg(q)

            @opik.track(name="e2e.query.rdf", project_name=OPIK_PROJECT)
            def _query_rdf(question):
                return rdf_client.ask(question, database=RDF_DATABASE, reasoning_mode=True, repair_budget=1)
            rdf_answer = _query_rdf(q)
        else:
            lpg_answer = lpg_client.ask(q, database=LPG_DATABASE, reasoning_mode=True, repair_budget=1)
            rdf_answer = rdf_client.ask(q, database=RDF_DATABASE, reasoning_mode=True, repair_budget=1)

        query_results.append({
            "id": doc["id"],
            "question": q,
            "expected": doc["expected_answer"],
            "lpg_answer": lpg_answer[:200],
            "rdf_answer": rdf_answer[:200],
            "category": doc["category"],
        })
        print(f"    LPG: {lpg_answer[:100]}...")
        print(f"    RDF: {rdf_answer[:100]}...")

    # --- Phase 3: Summary ---
    print(f"\n{'─' * 70}")
    print("Phase 3: Summary")
    print(f"{'─' * 70}")

    total_lpg_nodes = sum(r["nodes"] for r in lpg_results)
    total_rdf_nodes = sum(r["nodes"] for r in rdf_results)

    print(f"\n  LPG ({LPG_DATABASE}):")
    print(f"    Total nodes: {total_lpg_nodes}")
    print(f"    Success: {sum(1 for r in lpg_results if r['status'] == 'active')}/{len(lpg_results)}")

    print(f"\n  RDF ({RDF_DATABASE}):")
    print(f"    Total nodes: {total_rdf_nodes}")
    print(f"    Success: {sum(1 for r in rdf_results if r['status'] == 'active')}/{len(rdf_results)}")

    print(f"\n  Queries: {len(query_results)} completed")

    # --- Save results ---
    output_dir = Path(__file__).parent / "datasets" / "results"
    output_dir.mkdir(exist_ok=True)

    with open(output_dir / "e2e_results.json", "w") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "lpg_database": LPG_DATABASE,
            "rdf_database": RDF_DATABASE,
            "dataset_source": dataset_source,
            "dataset_count": len(normalized_dataset),
            "model": MODEL,
            "workspaces": {"lpg": lpg_client.workspace_id, "rdf": rdf_client.workspace_id},
            "indexing": {"lpg": lpg_results, "rdf": rdf_results},
            "queries": query_results,
        }, f, indent=2)
    print(f"\n  Results saved to {output_dir / 'e2e_results.json'}")

    # --- Flush Opik ---
    if opik_enabled:
        time.sleep(2)
        try:
            opik.flush_tracker()
        except Exception:
            pass
        print(f"\n  Opik traces: https://www.comet.com/opik/tteon/{OPIK_PROJECT}")

    store.close()
    print(f"\n{'=' * 70}")
    print("E2E Evaluation Complete")
    print(f"{'=' * 70}")


def _ensure_database(store, db_name: str) -> None:
    """Create database if it doesn't exist."""
    try:
        with store._driver.session(database="system") as session:
            result = session.run("SHOW DATABASES")
            existing = {r["name"] for r in result}
            if db_name not in existing:
                session.run(f"CREATE DATABASE {db_name} IF NOT EXISTS")
                print(f"  Created database: {db_name}")
                time.sleep(2)  # wait for DB to come online
            else:
                print(f"  Database exists: {db_name}")
    except Exception as exc:
        print(f"  Database check failed: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a small FinDER E2E evaluation against SEOCHO.")
    parser.add_argument(
        "--dataset-source",
        choices=("sample", "hf"),
        default=os.getenv("FINDER_DATASET_SOURCE", "sample"),
        help="Use the local sample dataset or the gated Hugging Face FinDER dataset.",
    )
    parser.add_argument(
        "--split",
        default=os.getenv("FINDER_HF_SPLIT", HF_SPLIT),
        help="Hugging Face split/slice expression, e.g. 'train[:5]'. Ignored for sample mode.",
    )
    args = parser.parse_args()
    main(dataset_source=args.dataset_source, split=args.split)
