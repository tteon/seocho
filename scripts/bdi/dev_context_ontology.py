#!/usr/bin/env python3
"""BDI ontology for seocho development context — dogfooding example.

Defines the ontology for the development decision knowledge graph and
loads extracted BDI triples into a seocho graph store.

Usage::

    # 1. Extract graph from Obsidian
    python scripts/bdi/extract_context_graph.py \\
        --vault ~/my_local_work/obsidian/seocho \\
        --output /tmp/bdi-context.json

    # 2. Load into graph (requires running Neo4j)
    python scripts/bdi/dev_context_ontology.py \\
        --input /tmp/bdi-context.json \\
        --database dev_context

    # 3. Query
    python -c "
    from seocho import Seocho
    from seocho.store import Neo4jGraphStore
    s = Seocho(
        ontology=__import__('scripts.bdi.dev_context_ontology', fromlist=['ontology']).ontology,
        graph_store=Neo4jGraphStore('bolt://localhost:7687', 'neo4j', 'password'),
        llm=__import__('seocho.store.llm', fromlist=['create_llm_backend']).create_llm_backend(),
    )
    print(s.ask('왜 SemanticAgentFlow를 SDK로 옮겼나?', database='dev_context'))
    "
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from seocho.ontology import Ontology, NodeDef, RelDef, Property


# ---------------------------------------------------------------------------
# BDI Ontology for Development Context
# ---------------------------------------------------------------------------

ontology = Ontology(
    name="seocho_dev_context",
    description="BDI knowledge graph for seocho development decisions and reasoning",
    version="1.0.0",
    nodes={
        "Topic": NodeDef(
            description="Wiki topic page — a unit of knowledge",
            properties={
                "label": Property(str, description="Topic identifier"),
                "file": Property(str, description="Source .md filename"),
            },
        ),
        "Belief": NodeDef(
            description="What the team holds true — grounded in evidence",
            properties={
                "label": Property(str, description="Human-readable belief statement"),
                "status": Property(str, description="active or invalidated"),
            },
        ),
        "Desire": NodeDef(
            description="What the team wants to achieve",
            properties={
                "label": Property(str, description="Desired outcome"),
            },
        ),
        "Intention": NodeDef(
            description="What the team commits to executing",
            properties={
                "label": Property(str, description="Committed action"),
                "status": Property(str, description="active, completed, or abandoned"),
            },
        ),
        "Plan": NodeDef(
            description="Concrete plan — typically an ADR",
            properties={
                "label": Property(str, description="Plan/ADR identifier"),
            },
        ),
        "Task": NodeDef(
            description="Execution unit tracked in .beads",
            properties={
                "beads_id": Property(str, unique=True, description=".beads issue ID"),
            },
        ),
        "Evidence": NodeDef(
            description="Factual evidence grounding a belief",
            properties={
                "label": Property(str, description="Evidence identifier"),
                "source": Property(str, description="Source file or URL"),
            },
        ),
    },
    relationships={
        "RELATED_TO": RelDef(source="Topic", target="Topic", description="Topical connection"),
        "HAS_BELIEF": RelDef(source="Topic", target="Belief", description="Topic contains this belief"),
        "HAS_DESIRE": RelDef(source="Topic", target="Desire", description="Topic contains this desire"),
        "HAS_INTENTION": RelDef(source="Topic", target="Intention", description="Topic contains this intention"),
        "MOTIVATES": RelDef(source="Belief", target="Desire", description="This belief motivates this desire"),
        "FULFILS": RelDef(source="Intention", target="Desire", description="This intention fulfils this desire"),
        "SUPPORTED_BY": RelDef(source="Intention", target="Belief", description="This intention relies on this belief"),
        "SPECIFIES": RelDef(source="Intention", target="Plan", description="This intention details this plan"),
        "GROUNDED_IN": RelDef(source="Belief", target="Evidence", description="This belief is grounded in this evidence"),
        "INVALIDATES": RelDef(source="Belief", target="Belief", description="This belief invalidates another"),
        "REFERENCES": RelDef(source="Topic", target="Plan", description="Topic references this ADR/plan"),
        "TRACKED_BY": RelDef(source="Topic", target="Task", description="Topic tracked by this beads issue"),
    },
)


def load_graph(input_path: str, database: str = "dev_context") -> None:
    """Load extracted BDI graph into Neo4j via seocho."""
    from seocho.store.graph import Neo4jGraphStore

    data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")

    try:
        summary = store.write(
            data["nodes"],
            data["relationships"],
            database=database,
            workspace_id="dev_context",
            source_id="bdi-extraction",
        )
        print(f"Loaded: {summary.get('nodes_created', 0)} nodes, "
              f"{summary.get('relationships_created', 0)} relationships",
              file=sys.stderr)
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description="Load BDI context graph into seocho")
    parser.add_argument("--input", required=True, help="JSON file from extract_context_graph.py")
    parser.add_argument("--database", default="dev_context", help="Target Neo4j database")
    args = parser.parse_args()
    load_graph(args.input, args.database)


if __name__ == "__main__":
    main()
