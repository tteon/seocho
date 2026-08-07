#!/usr/bin/env python3
"""Offline Owlready2 governance arm for the entity-management ablation."""
from __future__ import annotations

import gzip
import importlib.util
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

from owlready2 import AllDisjoint, Thing, get_ontology

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
CLEAN = BASE / "log2026-clean-entity-network-v1"
GRAPH = BASE / "log2026-full-multiagent-network-v1/collapsed_graph.json.gz"
PROVIDERS = BASE / "log2026-sdcr-null-v1/provider_category_graphs.json.gz"
OUT = BASE / "log2026-ontology-governance-ablation-v1"
FORBIDDEN_LABELS = {"Regulator", "Person", "FinancialMetric"}
CORPORATE_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "plc", "ltd", "limited", "llc"}


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe_name(value: str) -> str:
    return "e_" + re.sub(r"[^a-z0-9_]", "_", value.lower()).strip("_")[:120]


def canonical_name(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", value.lower())
    while tokens and tokens[-1] in CORPORATE_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def build_ontology(path: Path) -> Any:
    ontology = get_ontology("https://seocho.local/log2026/entity-governance.owl")
    with ontology:
        class Entity(Thing): pass
        class LegalEntity(Entity): pass
        class Organization(Entity): pass
        class Subsidiary(LegalEntity): pass
        class Regulator(Organization): pass
        class Person(Entity): pass
        class FinancialMetric(Entity): pass
        class CanonicalCandidate(Entity): pass
        class QuarantinedEntity(Entity): pass
        AllDisjoint([LegalEntity, Person, FinancialMetric])
        AllDisjoint([CanonicalCandidate, QuarantinedEntity])
    ontology.save(file=str(path), format="rdfxml")
    return ontology


def govern(clean: dict[str, Any], ontology: Any) -> tuple[list[str], list[dict[str, Any]]]:
    aliases = {alias for item in clean["identity_registry"]["accepted"].values() for alias in item["aliases"]}
    alias_support = {
        alias: {
            "canonical_id": canonical_id,
            "providers": item["providers"],
            "categories": item["categories"],
        }
        for canonical_id, item in clean["identity_registry"]["accepted"].items()
        for alias in item["aliases"]
    }
    receipts = []
    eligible = []
    with ontology:
        for row in clean["entity_audit"]:
            if not row["included"] or canonical_name(row["entity"]) not in aliases:
                continue
            labels = set(row["labels"])
            forbidden = sorted(labels & FORBIDDEN_LABELS)
            decision = "quarantine" if forbidden else "accept"
            individual = ontology.Entity(safe_name(row["entity"]))
            individual.is_a.append(ontology.QuarantinedEntity if forbidden else ontology.CanonicalCandidate)
            receipts.append({
                "entity": row["entity"], "labels": sorted(labels), "decision": decision,
                "rules": ["forbidden_type_disjointness"] if forbidden else ["identifier_support", "compatible_type"],
                "forbidden_labels": forbidden,
                "identity_support": alias_support[canonical_name(row["entity"])],
                "ontology_version": "log2026.entity-governance.v1",
                "execution_boundary": "offline",
            })
            if not forbidden:
                eligible.append(row["entity"])
    rows_by_name = {row["entity"]: row for row in clean["entity_audit"]}
    eligible.sort(key=lambda name: (-len(rows_by_name[name]["categories"]),
                                    -int(rows_by_name[name]["observations"]), name))
    return eligible[:50], receipts


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    clean = json.loads((CLEAN / "analysis.json").read_text())
    ontology = build_ontology(OUT / "entity_governance.owl")
    selected, receipts = govern(clean, ontology)
    ontology.save(file=str(OUT / "entity_governance_with_receipts.owl"), format="rdfxml")

    with gzip.open(GRAPH, "rt") as handle:
        graph = json.load(handle)
    with gzip.open(PROVIDERS, "rt") as handle:
        providers = json.load(handle)["graphs"]
    network = load_module("network27", ROOT / "examples/mdm/27_full_finder_multiagent_network.py")
    nullmod = load_module("null29", ROOT / "examples/mdm/29_sdcr_matched_null.py")
    observed = network.local_and_ppr_divergence(graph["nodes"], graph["edges"], selected)
    retrieved = nullmod.ppr_neighbors(providers, set(selected))
    null_rows = nullmod.null_rows(retrieved)
    summary = nullmod.summarize(null_rows, observed)
    compare = load_module("compare48", ROOT / "examples/mdm/48_entity_cleaning_ablation.py")
    clean_observed = [
        {**row, "rank_weighted_divergence": compare.rank_divergence(row["left_top"], row["right_top"])}
        for row in clean["entity_context_divergence"]
    ]
    governed_observed = [
        {**row, "rank_weighted_divergence": compare.rank_divergence(row["left_top"], row["right_top"])}
        for row in observed
    ]
    bootstrap_delta = compare.bootstrap_auc_delta(
        clean["null_rows"], clean_observed, null_rows, governed_observed
    )
    summary.update({
        "selected_entities": len(selected),
        "accepted_receipts": sum(r["decision"] == "accept" for r in receipts),
        "quarantined_receipts": sum(r["decision"] == "quarantine" for r in receipts),
        "mean_ppr20": round(mean(r["ppr20_divergence"] for r in observed), 6),
        "median_ppr20": round(median(r["ppr20_divergence"] for r in observed), 6),
        "auroc_delta_vs_identifier": round(summary["auroc"] - clean["summary"]["auroc"], 6),
        "auroc_delta_entity_clustered_bootstrap_95_ci": [
            round(bootstrap_delta[int(0.025 * len(bootstrap_delta))], 6),
            round(bootstrap_delta[int(0.975 * len(bootstrap_delta))], 6),
        ],
    })
    payload = {
        "contract": "log2026.ontology_entity_governance_ablation.v1",
        "execution_boundary": "offline only; no runtime or SDCR trigger dependency",
        "ontology_engine": "Owlready2",
        "rules": [
            "identifier support is inherited from the identifier-constrained arm",
            "LegalEntity/Organization are compatible issuer types",
            "Regulator/Person/FinancialMetric co-typing quarantines an ambiguous alias",
            "CanonicalCandidate and QuarantinedEntity are disjoint",
        ],
        "summary": summary, "selected_entities": selected,
        "decision_receipts": receipts, "entity_context_divergence": observed, "null_rows": null_rows,
        "limitations": [
            "The current graph has no CIK or canonical-ID property.",
            "No independent SAME_AS/NOT_SAME_AS gold set is available, so false-merge accuracy is not claimed.",
            "Rules are compiled and applied offline; no Java reasoner runs in the answering path.",
        ],
    }
    (OUT / "analysis.json").write_text(json.dumps(payload, indent=2) + "\n")
    lines = ["# Offline Ontology-Governed Entity Ablation", "",
             f"- Selected issuer seeds: {len(selected)}", f"- Quarantined aliases: {summary['quarantined_receipts']}",
             f"- Cross-category pairs: {summary['cross_view_pairs']}", f"- Matched-null pairs: {summary['null_pairs']}",
             f"- AUROC: {summary['auroc']}", f"- Mean PPR@20 divergence: {summary['mean_ppr20']}", "",
             "Owlready2 is used only to compile and audit offline type/disjointness policy. It cannot trigger SDCR.", ""]
    (OUT / "analysis.md").write_text("\n".join(lines))
    print(OUT / "analysis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
