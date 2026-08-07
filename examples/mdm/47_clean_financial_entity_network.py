#!/usr/bin/env python3
"""Re-analyse cross-category context using auditable financial-entity seeds.

The original exploratory analysis admitted every repeated noun phrase.  This
script keeps the graph unchanged but restricts comparison seeds to named legal
entities, removes filing placeholders and generic organization words, and
recomputes both observed divergence and the matched cross-model null from the
existing read-only exports.
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FEDCAT = ROOT / "outputs/evaluation/mdm_fedcat"
SOURCE = FEDCAT / "log2026-full-multiagent-network-v1/collapsed_graph.json.gz"
PROVIDERS = FEDCAT / "log2026-sdcr-null-v1/provider_category_graphs.json.gz"
OUT = FEDCAT / "log2026-clean-entity-network-v1"

GENERIC_EXACT = {
    "company", "companies", "corporation", "corporate", "entity", "filing company",
    "filing entity", "reporting entity", "reporting company", "issuer", "group",
    "management", "company 10 k filer", "company unnamed", "unnamed company",
    "unknown company", "unnamedentity", "company name", "the corporation", "inc", "our",
    "securities and exchange commission", "sec",
}
GENERIC_PARTS = {
    "company", "companies", "corporation", "entity", "filing", "issuer", "unnamed",
    "unknown", "reporting", "management", "group", "inc", "corp", "corporate",
}
LEGAL_LABELS = {"LegalEntity", "Organization"}
CORPORATE_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "plc", "ltd", "limited", "llc"}


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entity_decision(name: str, labels: set[str]) -> tuple[bool, str]:
    """Return a deterministic, answer-blind inclusion decision and reason."""
    if not labels.intersection(LEGAL_LABELS):
        return False, "no_legal_entity_label"
    if name in GENERIC_EXACT:
        return False, "generic_placeholder"
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    if not tokens or all(token in GENERIC_PARTS or token.isdigit() for token in tokens):
        return False, "generic_organization_phrase"
    if len(tokens) == 1 and len(tokens[0]) < 2:
        return False, "too_short"
    return True, "named_financial_entity"


def normalized_name(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", value.lower())
    while tokens and tokens[-1] in CORPORATE_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def export_identity_observations() -> list[dict[str, Any]]:
    """Read identity evidence without mutating any provider database."""
    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    import yaml

    load_dotenv(ROOT / ".env")
    config = yaml.safe_load((ROOT / "examples/mdm/config/provider_databases.yaml").read_text())
    mapping = network_workspace_categories()
    auth = (os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"])
    prefix = "fedcat-scenario-duplicate-aware-survivorship-v1-fibo-finance-core-"
    rows: list[dict[str, Any]] = []
    for provider, spec in config["instances"].items():
        driver = GraphDatabase.driver(spec["uri"], auth=auth)
        try:
            with driver.session(database=spec["database"]) as session:
                result = session.run(
                    "MATCH (n) WHERE n._workspace_id STARTS WITH $prefix "
                    "AND any(label IN labels(n) WHERE label IN ['LegalEntity','Organization']) "
                    "RETURN n._workspace_id AS workspace, coalesce(n.name,'') AS name, "
                    "coalesce(n.ticker,'') AS ticker, labels(n) AS labels",
                    prefix=prefix,
                )
                for row in result:
                    name = normalized_name(str(row["name"] or ""))
                    ticker = re.sub(r"[^A-Z0-9.-]", "", str(row["ticker"] or "").upper())
                    category = mapping.get(str(row["workspace"] or ""))
                    if name and category:
                        rows.append({"provider": provider, "category": category, "name": name,
                                     "ticker": ticker or None, "labels": sorted(row["labels"] or [])})
        finally:
            driver.close()
    return rows


def network_workspace_categories() -> dict[str, str]:
    result: dict[str, str] = {}
    partials = FEDCAT / "fedcat-full-all-survivorship-v1/index_partial"
    for path in partials.glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if not row.get("error"):
            result[str(row["workspace_id"])] = str(row["category"])
    return result


def build_identity_registry(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Build conservative ticker-led alias clusters with conflict receipts."""
    name_tickers: dict[str, set[str]] = defaultdict(set)
    pair_support: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(
        lambda: {"providers": set(), "categories": set()}
    )
    for row in observations:
        if row["ticker"]:
            name_tickers[row["name"]].add(row["ticker"])
            pair_support[(row["name"], row["ticker"])]["providers"].add(row["provider"])
            pair_support[(row["name"], row["ticker"])]["categories"].add(row["category"])
    alias_to_id: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    for name, tickers in sorted(name_tickers.items()):
        if len(tickers) == 1:
            ticker = next(iter(tickers))
            evidence = pair_support[(name, ticker)]
            if len(evidence["providers"]) >= 2 or len(evidence["categories"]) >= 2:
                alias_to_id[name] = "ticker:" + ticker
        else:
            conflicts.append({"alias": name, "tickers": sorted(tickers), "decision": "quarantine"})
    support: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"providers": set(), "categories": set(), "aliases": set()})
    for row in observations:
        canonical = alias_to_id.get(row["name"])
        if canonical:
            support[canonical]["providers"].add(row["provider"])
            support[canonical]["categories"].add(row["category"])
            support[canonical]["aliases"].add(row["name"])
    accepted = {
        key: {field: sorted(values) for field, values in value.items()}
        for key, value in support.items()
        if len(value["providers"]) >= 2 or len(value["categories"]) >= 2
    }
    return {
        "policy": "ticker-name must-link requires >=2 providers or >=2 categories; conflicting tickers cannot-link and quarantine; canonical entity also requires >=2 providers or >=2 categories",
        "accepted": accepted,
        "conflicts": conflicts,
        "schema_gap": "No CIK/canonical identifier properties and no Organization labels were observed in the current graph export.",
    }


def matched_null(provider_graphs: dict[str, Any], names: set[str], null_module: Any) -> list[dict[str, Any]]:
    retrieved = null_module.ppr_neighbors(provider_graphs, names)
    return null_module.null_rows(retrieved)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--entity-limit", type=int, default=300)
    parser.add_argument("--reuse-identity-cache", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with gzip.open(SOURCE, "rt") as handle:
        graph = json.load(handle)
    nodes, edges = graph["nodes"], graph["edges"]
    network = load_module("network27", ROOT / "examples/mdm/27_full_finder_multiagent_network.py")
    null_module = load_module("null29", ROOT / "examples/mdm/29_sdcr_matched_null.py")

    identity_cache = args.output / "identity_observations.json.gz"
    if args.reuse_identity_cache and identity_cache.exists():
        with gzip.open(identity_cache, "rt") as handle:
            identity_observations = json.load(handle)
    else:
        identity_observations = export_identity_observations()
        with gzip.open(identity_cache, "wt") as handle:
            json.dump(identity_observations, handle)
    registry = build_identity_registry(identity_observations)
    accepted_aliases = {
        alias for item in registry["accepted"].values() for alias in item["aliases"]
    }

    categories: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, set[str]] = defaultdict(set)
    observations: Counter[str] = Counter()
    for node in nodes:
        categories[node["name"]].add(node["category"])
        labels[node["name"]].update(node.get("labels", []))
        observations[node["name"]] += int(node.get("observations", 0))

    audit_rows = []
    accepted = []
    for name in sorted(categories):
        if len(categories[name]) < 2:
            continue
        keep, reason = entity_decision(name, labels[name])
        if keep and normalized_name(name) not in accepted_aliases:
            keep, reason = False, "no_supported_canonical_identifier"
        audit_rows.append({
            "entity": name, "labels": sorted(labels[name]), "categories": sorted(categories[name]),
            "observations": observations[name], "included": keep, "reason": reason,
        })
        if keep:
            accepted.append(name)
    accepted.sort(key=lambda name: (-len(categories[name]), -observations[name], name))
    accepted = accepted[: args.entity_limit]

    observed_rows = network.local_and_ppr_divergence(nodes, edges, accepted)
    with gzip.open(PROVIDERS, "rt") as handle:
        provider_cache = json.load(handle)
    null_rows = matched_null(provider_cache["graphs"], set(accepted), null_module)
    summary = null_module.summarize(null_rows, observed_rows)
    summary.update({
        "eligible_repeated_phrases": len(audit_rows),
        "accepted_named_entities": len(accepted),
        "rejected_phrases": sum(not row["included"] for row in audit_rows),
        "observed_pair_mean_ppr20": round(mean(row["ppr20_divergence"] for row in observed_rows), 6) if observed_rows else None,
        "observed_pair_median_ppr20": round(median(row["ppr20_divergence"] for row in observed_rows), 6) if observed_rows else None,
    })
    payload = {
        "contract": "log2026.clean_financial_entity_network.v1",
        "selection_policy": {
            "answer_blind": True,
            "required_any_label": sorted(LEGAL_LABELS),
            "generic_exact_exclusions": sorted(GENERIC_EXACT),
            "description": "Repeated legal-entity names linked to a ticker-led company dictionary and supported by at least two providers or two categories; filing placeholders and identifier conflicts are excluded.",
        },
        "identity_registry": registry,
        "summary": summary,
        "selected_entities": accepted,
        "entity_audit": audit_rows,
        "entity_context_divergence": observed_rows,
        "null_rows": null_rows,
    }
    (args.output / "analysis.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Clean Financial-Entity Network Analysis", "",
        "This analysis uses entity types and a frozen placeholder exclusion list; answer scores are never consulted.", "",
        f"- Repeated phrases audited: {len(audit_rows):,}",
        f"- Named financial entities retained: {len(accepted):,}",
        f"- Cross-category view pairs: {len(observed_rows):,}",
        f"- Matched cross-model null pairs: {len(null_rows):,}",
        f"- Mean PPR@20 divergence: {summary['observed_pair_mean_ppr20']}",
        f"- Rank-weighted divergence AUROC: {summary['auroc']}", "",
        "| Entity | Categories | Observations |", "|---|---:|---:|",
    ]
    for name in accepted[:40]:
        lines.append(f"| {name} | {len(categories[name])} | {observations[name]} |")
    (args.output / "analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.output / "analysis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
