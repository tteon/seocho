#!/usr/bin/env python3
"""Category-level entity duplication and fact-conflict census for hq-42k.

This is the data-federation problem-definition artifact, not an answer
benchmark. It reads provider databases on one DozerDB DBMS (or any compatible
provider config), emits raw entity/fact inventories, and summarizes where
category-specific overlap, duplicate clusters, single-provider coverage, and
metric conflicts appear.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
for path in (MDM_ROOT, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dotenv import dotenv_values  # noqa: E402

for key, value in dotenv_values(ROOT / ".env").items():
    if value is not None:
        os.environ.setdefault(key, value)

from examples.mdm.agents.provider_agent import workspace_for  # noqa: E402
from examples.mdm.lib import federation  # noqa: E402
from examples.mdm.lib.normalize import is_token_prefix, norm_key, norm_tokens  # noqa: E402
from examples.mdm.lib.survivorship import SourceFact, load_ruleset, survive_numeric  # noqa: E402

INFRA = set(federation.INFRA_LABELS)


def _load_indexer():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "idx_providers", MDM_ROOT / "11_index_providers.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load 11_index_providers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _auth() -> tuple[str, str]:
    return (
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ.get("NEO4J_PASSWORD", ""),
    )


def _primary_label(labels: list[str]) -> str:
    business = [label for label in labels if label not in INFRA]
    if not business:
        return ""
    preferred = [
        "LegalEntity",
        "Entity",
        "Company",
        "Organization",
        "Issuer",
        "Security",
        "FinancialMetric",
    ]
    for label in preferred:
        if label in business:
            return label
    return sorted(business)[0]


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _read_provider_inventory(inst, cases: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    from neo4j import GraphDatabase

    case_meta = {case["case_id"]: case for case in cases}
    wanted_workspaces = [workspace_for(inst.dept, case["case_id"]) for case in cases]
    driver = GraphDatabase.driver(inst.uri, auth=_auth())
    entities: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    try:
        with driver.session(database=inst.database) as session:
            entity_rows = session.run(
                """
                MATCH (n)
                WHERE n._workspace_id IN $workspaces
                  AND n.name IS NOT NULL
                  AND n.value IS NULL
                  AND NOT any(label IN labels(n) WHERE label IN $infra)
                RETURN elementId(n) AS eid, labels(n) AS labels, properties(n) AS props,
                       n._workspace_id AS workspace_id
                """,
                workspaces=wanted_workspaces,
                infra=federation.INFRA_LABELS,
            ).data()
            fact_rows = session.run(
                """
                MATCH (m)
                WHERE m._workspace_id IN $workspaces
                  AND m.name IS NOT NULL
                  AND m.value IS NOT NULL
                  AND NOT any(label IN labels(m) WHERE label IN $infra)
                OPTIONAL MATCH (e)-[r]-(m)
                WHERE e.name IS NOT NULL
                  AND e.value IS NULL
                  AND NOT type(r) IN ['MENTIONS']
                  AND NOT any(label IN labels(e) WHERE label IN $infra)
                WITH m, collect(DISTINCT e.name)[0..5] AS linked
                RETURN elementId(m) AS eid, labels(m) AS labels, properties(m) AS props,
                       m._workspace_id AS workspace_id, linked AS linked_entities
                """,
                workspaces=wanted_workspaces,
                infra=federation.INFRA_LABELS,
            ).data()
    finally:
        driver.close()

    prefix = f"fedcat-{inst.dept}-"
    for row in entity_rows:
        ws = str(row["workspace_id"])
        case_id = ws[len(prefix) :] if ws.startswith(prefix) else ws
        meta = case_meta.get(case_id, {})
        props = row["props"] or {}
        labels = [label for label in (row["labels"] or []) if label not in INFRA]
        name = str(props.get("name") or "")
        normalized = norm_key(name)
        primary_label = _primary_label(labels)
        entities.append(
            {
                "category": meta.get("category", ""),
                "case_id": case_id,
                "query": meta.get("query", ""),
                "provider_id": inst.dept,
                "model": inst.model,
                "uri": inst.uri,
                "database": inst.database,
                "workspace_id": ws,
                "entity_id": str(row["eid"]),
                "labels": sorted(labels),
                "primary_label": primary_label,
                "name": name,
                "normalized_name": normalized,
                "business_key": f"{normalized}|{primary_label}",
                "origin_eid": props.get("origin_eid", ""),
                "origin_instance": props.get("origin_instance", ""),
                "props": {
                    key: value
                    for key, value in props.items()
                    if not str(key).startswith("_") and key not in {"name"}
                },
            }
        )

    for row in fact_rows:
        ws = str(row["workspace_id"])
        case_id = ws[len(prefix) :] if ws.startswith(prefix) else ws
        meta = case_meta.get(case_id, {})
        props = row["props"] or {}
        labels = [label for label in (row["labels"] or []) if label not in INFRA]
        metric = str(props.get("name") or "")
        facts.append(
            {
                "category": meta.get("category", ""),
                "case_id": case_id,
                "query": meta.get("query", ""),
                "provider_id": inst.dept,
                "model": inst.model,
                "uri": inst.uri,
                "database": inst.database,
                "workspace_id": ws,
                "fact_id": str(row["eid"]),
                "labels": sorted(labels),
                "metric_raw": metric,
                "metric_key": norm_key(metric),
                "value_raw": str(props.get("value") or ""),
                "period": str(props.get("period") or ""),
                "basis": str(props.get("basis") or ""),
                "segment": str(props.get("segment") or ""),
                "linked_entities": list(row.get("linked_entities") or []),
                "origin_eid": props.get("origin_eid", ""),
                "origin_instance": props.get("origin_instance", ""),
            }
        )
    return entities, facts


@dataclass
class _DSU:
    parent: list[int]

    @classmethod
    def create(cls, n: int) -> "_DSU":
        return cls(parent=list(range(n)))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _cluster_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity in entities:
        by_cat[entity["category"]].append(entity)

    for category, rows in sorted(by_cat.items()):
        dsu = _DSU.create(len(rows))
        exact_pairs: set[tuple[int, int]] = set()
        prefix_pairs: set[tuple[int, int]] = set()
        for i, j in combinations(range(len(rows)), 2):
            a, b = rows[i], rows[j]
            if not a["normalized_name"] or not b["normalized_name"]:
                continue
            same_label = a["primary_label"] == b["primary_label"]
            if same_label and a["normalized_name"] == b["normalized_name"]:
                exact_pairs.add((i, j))
                dsu.union(i, j)
                continue
            ta, tb = norm_tokens(a["name"]), norm_tokens(b["name"])
            if same_label and (is_token_prefix(ta, tb) or is_token_prefix(tb, ta)):
                prefix_pairs.add((i, j))
                dsu.union(i, j)

        grouped: dict[int, list[int]] = defaultdict(list)
        for idx in range(len(rows)):
            grouped[dsu.find(idx)].append(idx)
        for cluster_idx, members in enumerate(grouped.values(), 1):
            member_rows = [rows[idx] for idx in members]
            providers = sorted({row["provider_id"] for row in member_rows})
            names = sorted({row["name"] for row in member_rows if row["name"]})
            labels = sorted({row["primary_label"] for row in member_rows if row["primary_label"]})
            methods = []
            member_set = set(members)
            if any(i in member_set and j in member_set for i, j in exact_pairs):
                methods.append("exact")
            if any(i in member_set and j in member_set for i, j in prefix_pairs):
                methods.append("token_prefix")
            if not methods:
                methods.append("singleton")
            canonical_name = max(names, key=lambda name: (len(norm_tokens(name)), len(name), name)) if names else ""
            clusters.append(
                {
                    "cluster_id": f"{category.lower().replace(' ', '_')}-{cluster_idx:04d}",
                    "category": category,
                    "canonical_name": canonical_name,
                    "normalized_name": norm_key(canonical_name),
                    "labels": labels,
                    "providers": providers,
                    "provider_count": len(providers),
                    "member_count": len(member_rows),
                    "case_ids": sorted({row["case_id"] for row in member_rows}),
                    "match_methods": methods,
                    "members": [
                        {
                            "provider_id": row["provider_id"],
                            "model": row["model"],
                            "database": row["database"],
                            "case_id": row["case_id"],
                            "entity_id": row["entity_id"],
                            "name": row["name"],
                            "primary_label": row["primary_label"],
                        }
                        for row in sorted(
                            member_rows,
                            key=lambda item: (item["provider_id"], item["case_id"], item["entity_id"]),
                        )
                    ],
                }
            )
    return clusters


def _fact_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ruleset = load_ruleset()
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        key = (
            fact["category"],
            fact["case_id"],
            fact["metric_key"],
            fact["period"].lower(),
            fact["basis"].lower(),
        )
        grouped[key].append(fact)

    out = []
    for (category, case_id, metric_key, period, basis), rows in sorted(grouped.items()):
        provider_rows = [row for row in rows if row["value_raw"]]
        providers = sorted({row["provider_id"] for row in provider_rows})
        if not provider_rows:
            continue
        survived = survive_numeric(
            [SourceFact(source=f"{row['provider_id']}/{row['model']}", raw=row["value_raw"]) for row in provider_rows],
            panel_size=max(len(providers), 1),
            ruleset=ruleset,
        )
        if len(providers) < 2 and survived.status != "quarantine":
            continue
        out.append(
            {
                "category": category,
                "case_id": case_id,
                "metric_key": metric_key,
                "period": period,
                "basis": basis,
                "provider_count": len(providers),
                "providers": providers,
                "status": survived.status,
                "rule": survived.rule,
                "agreement_count": survived.agreement_count,
                "confidence": survived.confidence,
                "survivor": {
                    "value": survived.value,
                    "value_raw": survived.value_raw,
                    "source": survived.source,
                },
                "values": [
                    {
                        "provider_id": row["provider_id"],
                        "model": row["model"],
                        "value_raw": row["value_raw"],
                        "fact_id": row["fact_id"],
                    }
                    for row in provider_rows
                ],
            }
        )
    return out


def _category_census(
    entities: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    provider_ids: list[str],
) -> dict[str, Any]:
    categories = sorted({row["category"] for row in entities} | {row["category"] for row in facts})
    out: dict[str, Any] = {}
    for category in categories:
        ents = [row for row in entities if row["category"] == category]
        cat_clusters = [row for row in clusters if row["category"] == category]
        cat_facts = [row for row in facts if row["category"] == category]
        cat_conflicts = [row for row in conflicts if row["category"] == category]
        multi_provider_clusters = [row for row in cat_clusters if row["provider_count"] >= 2]
        duplicate_clusters = [row for row in cat_clusters if row["member_count"] >= 2]
        by_provider = {}
        for provider_id in provider_ids:
            p_ents = [row for row in ents if row["provider_id"] == provider_id]
            p_facts = [row for row in cat_facts if row["provider_id"] == provider_id]
            by_provider[provider_id] = {
                "entities": len(p_ents),
                "unique_entity_keys": len({row["business_key"] for row in p_ents}),
                "facts": len(p_facts),
            }
        out[category] = {
            "raw_entities": len(ents),
            "canonical_clusters": len(cat_clusters),
            "duplicate_clusters": len(duplicate_clusters),
            "multi_provider_clusters": len(multi_provider_clusters),
            "singleton_clusters": sum(1 for row in cat_clusters if row["member_count"] == 1),
            "duplication_ratio": round(1.0 - len(cat_clusters) / len(ents), 3) if ents else 0.0,
            "cross_model_cluster_rate": round(len(multi_provider_clusters) / len(cat_clusters), 3) if cat_clusters else 0.0,
            "raw_facts": len(cat_facts),
            "fact_conflict_groups": len(cat_conflicts),
            "quarantined_fact_groups": sum(1 for row in cat_conflicts if row["status"] == "quarantine"),
            "by_provider": by_provider,
            "top_duplicate_clusters": sorted(
                [
                    {
                        "cluster_id": row["cluster_id"],
                        "canonical_name": row["canonical_name"],
                        "member_count": row["member_count"],
                        "provider_count": row["provider_count"],
                        "providers": row["providers"],
                        "case_ids": row["case_ids"][:5],
                    }
                    for row in duplicate_clusters
                ],
                key=lambda row: (-row["provider_count"], -row["member_count"], row["canonical_name"]),
            )[:10],
        }
    return out


def _problem_queries(census: dict[str, Any], conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queries = []
    for category, row in sorted(census.items()):
        queries.append(
            {
                "query_id": f"{category.lower().replace(' ', '_')}_entity_overlap",
                "category": category,
                "intent": "entity_duplication_census",
                "question": f"Which provider models duplicated or uniquely extracted entities in {category}?",
                "required_artifacts": ["entity_inventory.jsonl", "entity_clusters.jsonl", "category_entity_census.json"],
                "routing_implication": "high duplicate/cross-model overlap favors survivorship; high singleton rate favors federation fallback",
                "priority_score": row["duplicate_clusters"] + row["multi_provider_clusters"],
            }
        )
        if row["fact_conflict_groups"]:
            queries.append(
                {
                    "query_id": f"{category.lower().replace(' ', '_')}_fact_conflicts",
                    "category": category,
                    "intent": "metric_fact_conflict",
                    "question": f"Which {category} metric facts disagree across model-provider databases?",
                    "required_artifacts": ["fact_inventory.jsonl", "fact_conflicts.jsonl"],
                    "routing_implication": "quarantined groups must surface unresolved evidence instead of being silently merged",
                    "priority_score": row["fact_conflict_groups"],
                }
            )
    for conflict in sorted(conflicts, key=lambda row: (-row["provider_count"], row["category"], row["metric_key"]))[:20]:
        queries.append(
            {
                "query_id": f"conflict_{conflict['category'].lower().replace(' ', '_')}_{conflict['case_id']}_{conflict['metric_key'][:20]}",
                "category": conflict["category"],
                "intent": "survivorship_drilldown",
                "question": (
                    f"For case {conflict['case_id']}, why did providers disagree on "
                    f"{conflict['metric_key']} ({conflict['period'] or 'no period'})?"
                ),
                "required_artifacts": ["fact_conflicts.jsonl"],
                "routing_implication": "answer path should include all source values plus survivorship/quarantine rule",
                "priority_score": conflict["provider_count"],
            }
        )
    return sorted(queries, key=lambda row: (-row["priority_score"], row["query_id"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--providers-config", default=str(MDM_ROOT / "config" / "provider_databases.yaml"))
    parser.add_argument("--run-prefix", default="fedcat-single-dbms-v1")
    parser.add_argument("--n-per-cat", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    indexer = _load_indexer()
    cases = indexer.load_cases_8cat(n_per_cat=args.n_per_cat, seed=args.seed)
    instances = federation.load_instances(Path(args.providers_config))
    out_dir = ROOT / "outputs" / "evaluation" / "mdm_fedcat" / args.run_prefix
    out_dir.mkdir(parents=True, exist_ok=True)

    entities: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    for inst in instances:
        provider_entities, provider_facts = _read_provider_inventory(inst, cases)
        print(
            f"  {inst.dept:<10} {inst.database:<14}: "
            f"{len(provider_entities)} entities, {len(provider_facts)} facts"
        )
        entities.extend(provider_entities)
        facts.extend(provider_facts)

    clusters = _cluster_entities(entities)
    conflicts = _fact_conflicts(facts)
    provider_ids = [inst.dept for inst in instances]
    census = _category_census(entities, facts, clusters, conflicts, provider_ids)
    problem_queries = _problem_queries(census, conflicts)

    _jsonl(out_dir / "entity_inventory.jsonl", entities)
    _jsonl(out_dir / "fact_inventory.jsonl", facts)
    _jsonl(out_dir / "entity_clusters.jsonl", clusters)
    _jsonl(out_dir / "fact_conflicts.jsonl", conflicts)
    (out_dir / "category_entity_census.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "run_prefix": args.run_prefix,
                "providers_config": str(Path(args.providers_config)),
                "providers": [
                    {
                        "provider_id": inst.dept,
                        "model": inst.model,
                        "uri": inst.uri,
                        "database": inst.database,
                    }
                    for inst in instances
                ],
                "categories": sorted(census),
                "n_cases": len(cases),
                "entity_count": len(entities),
                "fact_count": len(facts),
                "cluster_count": len(clusters),
                "fact_conflict_count": len(conflicts),
                "category_census": census,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "problem_queries.json").write_text(
        json.dumps(problem_queries, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(
        f"== wrote census: {len(entities)} entities, {len(facts)} facts, "
        f"{len(clusters)} clusters, {len(conflicts)} conflict groups =="
    )
    for category, row in census.items():
        print(
            f"{category:<20} entities={row['raw_entities']:<4} "
            f"clusters={row['canonical_clusters']:<4} dup={row['duplication_ratio']:.3f} "
            f"cross_model={row['cross_model_cluster_rate']:.3f} "
            f"fact_conflicts={row['fact_conflict_groups']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
