#!/usr/bin/env python3
"""Publish and activate the explicit ontology fixture for governed E2E runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.ontology.lifecycle import OntologyLifecycleStore, build_bundle_atomically


def fixture_ontology() -> Ontology:
    return Ontology(
        name="exchange-memory-fixture",
        version="1.0.0",
        nodes={
            "ExchangeIntent": NodeDef(properties={"id": P(str), "workspace": P(str)}),
            "ExchangeMemoryEvent": NodeDef(
                properties={
                    "id": P(str),
                    "workspace": P(str),
                    "step": P(str),
                    "sequence": P(int),
                    "provenance": P(str),
                }
            ),
        },
        relationships={
            "HAS_EVENT": RelDef(source="ExchangeIntent", target="ExchangeMemoryEvent")
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bundle", type=Path, required=True, help="New immutable bundle directory"
    )
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--workspace-id", default="seocho-text2cypher-e2e-fixture")
    parser.add_argument("--package-id", default="exchange-memory-fixture")
    args = parser.parse_args()
    built = build_bundle_atomically(fixture_ontology(), args.bundle)
    store = OntologyLifecycleStore(args.state_db)
    current = store.status(args.workspace_id, args.package_id).get("active")
    expected = (
        None if current is None else (int(current["generation"]), int(current["epoch"]))
    )
    token = 1 if current is None else int(current["fencing_token"]) + 1
    activated, active = store.activate(
        args.workspace_id,
        args.package_id,
        args.bundle,
        fencing_token=token,
        expected=expected,
    )
    if not activated or active is None:
        raise SystemExit("fixture bundle activation CAS failed")
    print(
        json.dumps(
            {
                "bundle": built,
                "active": active.__dict__,
                "workspace_id": args.workspace_id,
                "package_id": args.package_id,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
