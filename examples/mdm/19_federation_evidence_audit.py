#!/usr/bin/env python3
"""Generate evidence-facing audit reports for category federation runs.

This is a zero-LLM post-processing step over a completed
``category_federation_aggregate.json`` plus the currently projected category
databases.  It produces:

- category-level quality breakdown
- routing decision audit table
- abstain reason taxonomy
- entity cluster inspector samples for MDM/provenance review
- run dashboard with aligned baseline and category-federation metrics
- scenario registry summary for prompt/ontology/model experiment tracking
- context-efficiency and survivorship policy summaries
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
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

from examples.mdm.lib import federation  # noqa: E402
from examples.mdm.lib.normalize import is_token_prefix, norm_key, norm_tokens  # noqa: E402

INFRA = set(federation.INFRA_LABELS)
GENERIC_CLUSTER_NAMES = {
    "company",
    "the company",
    "registrant",
    "the registrant",
    "management",
    "chief information officer",
    "chief information security officer",
    "chief financial officer",
    "general counsel",
}


def _auth() -> tuple[str, str]:
    return (
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ.get("NEO4J_PASSWORD", ""),
    )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_if_exists(path: Path) -> Any | None:
    if not path.exists():
        return None
    return _load_json(path)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * pct))))
    return ordered[index]


def _context_efficiency(lane: dict[str, Any]) -> float:
    ctx_chars = int(lane.get("ctx_chars") or 0)
    if ctx_chars <= 0:
        return 0.0
    return round(float(lane.get("token_f1") or 0.0) / ctx_chars * 1000, 4)


def _lane_with_efficiency(lane: dict[str, Any] | None) -> dict[str, Any]:
    lane = dict(lane or {})
    lane["context_efficiency_per_1k_chars"] = _context_efficiency(lane)
    return lane


def _primary_label(labels: list[str]) -> str:
    business = [label for label in labels if label not in INFRA]
    for preferred in (
        "LegalEntity",
        "Company",
        "Issuer",
        "Security",
        "FinancialMetric",
        "Metric",
        "Entity",
    ):
        if preferred in business:
            return preferred
    return sorted(business)[0] if business else "Entity"


def _category_breakdown(records: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for category in sorted({row["category"] for row in records}):
        rows = [row for row in records if row["category"] == category]
        out[category] = {
            "n": len(rows),
            "token_f1": round(
                sum(row["evaluation"].get("token_f1", 0.0) for row in rows) / len(rows), 3
            ),
            "number_overlap": round(
                sum(row["evaluation"].get("number_overlap_ratio", 0.0) for row in rows)
                / len(rows),
                3,
            ),
            "abstain": round(sum(1 for row in rows if row.get("abstain")) / len(rows), 3),
            "avg_context_chars": int(sum(row.get("context_chars", 0) for row in rows) / len(rows)),
            "routing_modes": dict(Counter(row.get("routing_mode", "") for row in rows)),
        }
    return out


def _summarize_scenario_registry(scenario_aggregate: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not scenario_aggregate:
        return []
    registry = []
    for scenario in scenario_aggregate.get("scenarios", []):
        results = scenario.get("results", [])
        latencies = [float(row.get("latency_s") or 0.0) for row in results if row.get("latency_s") is not None]
        candidate = scenario.get("candidate_census") or {}
        baseline = scenario.get("baseline_census") or {}
        gate = scenario.get("gate") or {}
        registry.append(
            {
                "scenario_id": scenario.get("scenario_id", ""),
                "prompt_id": (scenario.get("prompt") or {}).get("prompt_id", ""),
                "prompt_intent": (scenario.get("prompt") or {}).get("intent", ""),
                "ontology_id": (scenario.get("ontology") or {}).get("ontology_id", ""),
                "ontology_modules": (scenario.get("ontology") or {}).get("modules", []),
                "case_count": len(scenario.get("cases", [])),
                "provider_count": len({row.get("provider_id") for row in results if row.get("provider_id")}),
                "result_count": len(results),
                "error_count": sum(1 for row in results if row.get("error")),
                "nodes_created": sum(int(row.get("nodes_created") or 0) for row in results),
                "relationships_created": sum(int(row.get("rels_created") or 0) for row in results),
                "avg_latency_s": round(_mean(latencies), 2),
                "p90_latency_s": round(_percentile(latencies, 0.90), 2),
                "facts": int(candidate.get("facts") or 0),
                "baseline_facts": int(baseline.get("facts") or 0),
                "fact_gain": int(gate.get("fact_gain") or 0),
                "entities": int(candidate.get("entities") or 0),
                "cross_provider_clusters": int(candidate.get("cross_provider_clusters") or 0),
                "cross_provider_cluster_gain": int(gate.get("cross_provider_cluster_gain") or 0),
                "duplicate_ratio": candidate.get("duplicate_ratio", 0.0),
                "generic_entity_ratio": candidate.get("generic_entity_ratio", 0.0),
                "promotion_verdict": "promote" if gate.get("promote_to_full_reindex") else "hold",
                "promotion_rule": gate.get("rule", ""),
            }
        )
    return sorted(
        registry,
        key=lambda row: (
            row["promotion_verdict"] != "promote",
            -row["facts"],
            -row["cross_provider_clusters"],
            row["scenario_id"],
        ),
    )


def _run_dashboard(
    *,
    aggregate: dict[str, Any],
    baseline_aggregate: dict[str, Any] | None,
    scenario_registry: list[dict[str, Any]],
    run_prefix: str,
    baseline_run_prefix: str,
) -> dict[str, Any]:
    category_lane = _lane_with_efficiency((aggregate.get("lanes") or {}).get("category-federation"))
    baseline_lanes = (baseline_aggregate or {}).get("lanes") or {}
    baseline_federation = _lane_with_efficiency(baseline_lanes.get("federation"))
    best_silo_name = ""
    best_silo = {}
    for name, lane in baseline_lanes.items():
        if not str(name).startswith("silo-"):
            continue
        if not best_silo or float(lane.get("token_f1") or 0.0) > float(best_silo.get("token_f1") or 0.0):
            best_silo_name = str(name)
            best_silo = dict(lane)
    best_silo = _lane_with_efficiency(best_silo)
    token_delta = round(
        float(category_lane.get("token_f1") or 0.0) - float(baseline_federation.get("token_f1") or 0.0),
        3,
    )
    abstain_delta = round(
        float(category_lane.get("abstain") or 0.0) - float(baseline_federation.get("abstain") or 0.0),
        3,
    )
    context_delta = int(category_lane.get("ctx_chars") or 0) - int(baseline_federation.get("ctx_chars") or 0)
    quality_per_context_delta = round(
        float(category_lane.get("context_efficiency_per_1k_chars") or 0.0)
        - float(baseline_federation.get("context_efficiency_per_1k_chars") or 0.0),
        4,
    )
    return {
        "run_prefix": run_prefix,
        "baseline_run_prefix": baseline_run_prefix if baseline_aggregate else "",
        "case_count": int(aggregate.get("n_cases") or category_lane.get("n") or 0),
        "topology": aggregate.get("topology", ""),
        "selector_enabled": bool(aggregate.get("llm_selector_enabled")),
        "category_federation": category_lane,
        "provider_db_federation": baseline_federation,
        "best_silo_name": best_silo_name,
        "best_silo": best_silo,
        "deltas_vs_provider_federation": {
            "token_f1": token_delta,
            "abstain": abstain_delta,
            "context_chars": context_delta,
            "context_efficiency_per_1k_chars": quality_per_context_delta,
        },
        "beats_provider_federation": token_delta >= 0,
        "beats_best_silo": float(category_lane.get("token_f1") or 0.0) >= float(best_silo.get("token_f1") or 0.0),
        "partial_failure_degradation": (baseline_aggregate or {}).get("partial_failure_degradation", {}),
        "scenario_count": len(scenario_registry),
        "promoted_scenario_count": sum(1 for row in scenario_registry if row["promotion_verdict"] == "promote"),
    }


def _reason_for_abstain(row: dict[str, Any]) -> str:
    if not row.get("abstain"):
        return "answered"
    if row.get("error"):
        return "runtime_error"
    if row.get("selector_error"):
        return "selector_error"
    selected = row.get("effective_selected_providers") or row.get("selected_providers") or []
    if not selected:
        return "no_provider_evidence"
    missing = " ".join(str(slot).lower() for slot in (row.get("selector") or {}).get("missing_slots", []))
    if missing:
        if any(token in missing for token in ("period", "fy", "year", "quarter", "date")):
            return "missing_period"
        if any(token in missing for token in ("issuer", "company", "ticker", "registrant", "entity")):
            return "issuer_alias_mismatch"
        if any(token in missing for token in ("metric", "revenue", "margin", "eps", "income", "cash", "debt")):
            return "missing_metric"
        return "missing_required_slot"
    fact_counts = row.get("provider_fact_counts") or {}
    if sum(int(value or 0) for value in fact_counts.values()) == 0:
        return "retrieval_too_narrow"
    survivorship = row.get("survivorship") or {}
    if survivorship.get("quarantined"):
        return "conflicting_facts"
    answer = str(row.get("answer") or "").lower()
    if "not in the provided context" in answer or "not provided" in answer:
        return "grounded_context_gap"
    return "synthesis_refusal"


def _routing_audit(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in records:
        selector = row.get("selector") or {}
        rows.append(
            {
                "case_id": row["case_id"],
                "category": row["category"],
                "query": row["query"],
                "routing_mode": row.get("routing_mode", ""),
                "selected_providers": row.get("selected_providers", []),
                "effective_selected_providers": row.get("effective_selected_providers", []),
                "missing_slots": selector.get("missing_slots", []),
                "required_evidence": selector.get("required_evidence", []),
                "selector_rationale": selector.get("rationale", ""),
                "provider_fact_counts": row.get("provider_fact_counts", {}),
                "provider_node_counts": row.get("provider_node_counts", {}),
                "token_f1": row["evaluation"].get("token_f1", 0.0),
                "number_overlap": row["evaluation"].get("number_overlap_ratio", 0.0),
                "abstain": bool(row.get("abstain")),
                "abstain_reason": _reason_for_abstain(row),
                "answer_preview": " ".join(str(row.get("answer") or "").split())[:260],
            }
        )
    return sorted(rows, key=lambda item: (item["category"], item["case_id"]))


def _cluster_rows_for_category(uri: str, database: str) -> list[dict[str, Any]]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=_auth())
    try:
        with driver.session(database=database) as session:
            rows = session.run(
                """
                MATCH (n)
                WHERE n.case_id IS NOT NULL
                  AND n.provider_id IS NOT NULL
                  AND n.name IS NOT NULL
                  AND NOT any(label IN labels(n) WHERE label IN $infra)
                RETURN elementId(n) AS eid, labels(n) AS labels, properties(n) AS props
                """,
                infra=federation.INFRA_LABELS,
            ).data()
    finally:
        driver.close()
    out = []
    for row in rows:
        props = row["props"] or {}
        name = str(props.get("name") or "")
        out.append(
            {
                "entity_id": row["eid"],
                "labels": [label for label in (row["labels"] or []) if label not in INFRA],
                "primary_label": _primary_label(row["labels"] or []),
                "name": name,
                "normalized_name": norm_key(name),
                "provider_id": str(props.get("provider_id") or ""),
                "model": str(props.get("model") or ""),
                "case_id": str(props.get("case_id") or ""),
                "category": str(props.get("category") or ""),
                "value": props.get("value"),
                "period": str(props.get("period") or ""),
                "basis": str(props.get("basis") or ""),
                "unit": str(props.get("unit") or ""),
                "source_scenario_id": str(props.get("source_scenario_id") or ""),
                "source_provider_eid": str(props.get("source_provider_eid") or ""),
            }
        )
    return out


def _cluster_entities(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["normalized_name"]:
            grouped[(row["category"], row["primary_label"], row["normalized_name"])].append(row)

    prefix_groups: list[list[dict[str, Any]]] = []
    by_cat_label: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_cat_label[(row["category"], row["primary_label"])].append(row)
    for (_category, _label), items in by_cat_label.items():
        for i, left in enumerate(items):
            left_tokens = norm_tokens(left["name"])
            if not left_tokens:
                continue
            members = [left]
            for right in items[i + 1 :]:
                right_tokens = norm_tokens(right["name"])
                if right_tokens and (is_token_prefix(left_tokens, right_tokens) or is_token_prefix(right_tokens, left_tokens)):
                    members.append(right)
            if len(members) > 1:
                prefix_groups.append(members)

    clusters = []
    seen: set[tuple[str, str, str]] = set()
    for (category, label, normalized), members in grouped.items():
        if len(members) < 2:
            continue
        seen.add((category, label, normalized))
        clusters.append(_format_cluster(category, label, members, "exact"))
    for members in prefix_groups:
        key = (members[0]["category"], members[0]["primary_label"], norm_key(members[0]["name"]))
        if key in seen:
            continue
        clusters.append(_format_cluster(members[0]["category"], members[0]["primary_label"], members, "token_prefix"))

    clusters = [
        cluster
        for cluster in clusters
        if cluster["provider_count"] >= 2 or cluster["case_count"] >= 2 or cluster["fact_member_count"] >= 2
    ]
    return sorted(clusters, key=_cluster_sort_key)[:limit]


def _format_cluster(category: str, label: str, members: list[dict[str, Any]], method: str) -> dict[str, Any]:
    names = sorted({member["name"] for member in members if member["name"]})
    canonical = max(names, key=lambda name: (len(norm_tokens(name)), len(name), name)) if names else ""
    return {
        "category": category,
        "canonical_name": canonical,
        "primary_label": label,
        "match_method": method,
        "member_count": len(members),
        "provider_count": len({member["provider_id"] for member in members}),
        "case_count": len({member["case_id"] for member in members}),
        "fact_member_count": sum(1 for member in members if member.get("value") is not None),
        "providers": sorted({member["provider_id"] for member in members}),
        "case_ids": sorted({member["case_id"] for member in members})[:8],
        "members": [
            {
                "provider_id": member["provider_id"],
                "model": member["model"],
                "case_id": member["case_id"],
                "name": member["name"],
                "value": member.get("value"),
                "period": member["period"],
                "basis": member["basis"],
                "unit": member["unit"],
                "source_provider_eid": member["source_provider_eid"],
            }
            for member in sorted(members, key=lambda row: (row["provider_id"], row["case_id"], row["name"]))
        ][:12],
    }


def _cluster_sort_key(cluster: dict[str, Any]) -> tuple[Any, ...]:
    normalized = norm_key(cluster["canonical_name"])
    generic_penalty = 1 if normalized in GENERIC_CLUSTER_NAMES or normalized.startswith("the company") else 0
    return (
        generic_penalty,
        -cluster["fact_member_count"],
        -cluster["provider_count"],
        -cluster["member_count"],
        cluster["category"],
        cluster["canonical_name"],
    )


def _entity_cluster_inspector(policy: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    clusters = []
    for category, spec in sorted(policy["categories"].items()):
        db = spec["category_database"]
        rows = _cluster_rows_for_category(db["uri"], db["database"])
        clusters.extend(_cluster_entities(rows, limit=limit))
    return sorted(clusters, key=_cluster_sort_key)[:limit]


def _fact_cluster_inspector(policy: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for _category, spec in sorted(policy["categories"].items()):
        db = spec["category_database"]
        for row in _cluster_rows_for_category(db["uri"], db["database"]):
            if row.get("value") is None:
                continue
            key = (row["category"], row["case_id"], norm_key(row["name"]))
            if key[2]:
                groups[key].append(row)
    clusters = []
    for (category, case_id, metric_key), members in groups.items():
        if len(members) < 2:
            continue
        providers = sorted({member["provider_id"] for member in members})
        values = sorted({str(member.get("value")) for member in members if member.get("value") is not None})
        periods = sorted({member["period"] for member in members if member["period"]})
        clusters.append(
            {
                "category": category,
                "case_id": case_id,
                "metric_key": metric_key,
                "canonical_metric": max(
                    {member["name"] for member in members},
                    key=lambda name: (len(norm_tokens(name)), len(name), name),
                ),
                "provider_count": len(providers),
                "member_count": len(members),
                "distinct_value_count": len(values),
                "providers": providers,
                "periods": periods[:8],
                "values": values[:8],
                "members": [
                    {
                        "provider_id": member["provider_id"],
                        "model": member["model"],
                        "name": member["name"],
                        "value": member.get("value"),
                        "period": member["period"],
                        "basis": member["basis"],
                        "unit": member["unit"],
                        "source_provider_eid": member["source_provider_eid"],
                    }
                    for member in sorted(members, key=lambda item: (item["provider_id"], item["period"], item["name"]))
                ][:16],
            }
        )
    return sorted(
        clusters,
        key=lambda item: (
            -item["provider_count"],
            -item["member_count"],
            item["distinct_value_count"],
            item["category"],
            item["canonical_metric"],
        ),
    )[:limit]


def _survivorship_policy_summary(fact_clusters: list[dict[str, Any]]) -> dict[str, Any]:
    consensus = [cluster for cluster in fact_clusters if cluster["distinct_value_count"] <= 1]
    conflicts = [cluster for cluster in fact_clusters if cluster["distinct_value_count"] > 1]
    multi_provider = [cluster for cluster in fact_clusters if cluster["provider_count"] >= 2]
    return {
        "reviewed_fact_clusters": len(fact_clusters),
        "multi_provider_fact_clusters": len(multi_provider),
        "consensus_clusters": len(consensus),
        "conflicting_value_clusters": len(conflicts),
        "recommended_policy": [
            "merge consensus numeric facts only when period, unit, and basis are compatible",
            "preserve provider-specific rows when values conflict or period/unit is missing",
            "prefer category-local survivorship over global entity collapse for legal, risk, and governance evidence",
            "keep provider_id, model, prompt_id, ontology_hash, source_scenario_id, case_id, period, unit, and source evidence as merge properties",
        ],
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# hq-42k Federation Evidence Audit",
        "",
        f"Generated: {payload['generated_at']}",
        f"Run prefix: `{payload['run_prefix']}`",
        "",
        "## Federation Run Dashboard",
        "",
        "| Lane | Cases | Token F1 | Number overlap | Abstain | Avg context chars | F1 / 1k ctx chars |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    dashboard = payload["run_dashboard"]
    for label, lane in (
        ("Provider-DB federation baseline", dashboard.get("provider_db_federation", {})),
        (f"Best silo baseline ({dashboard.get('best_silo_name') or '-'})", dashboard.get("best_silo", {})),
        ("Category federation", dashboard.get("category_federation", {})),
    ):
        if not lane:
            continue
        lines.append(
            f"| {label} | {int(lane.get('n') or dashboard.get('case_count') or 0)} | "
            f"{float(lane.get('token_f1') or 0.0):.3f} | {float(lane.get('overlap') or 0.0):.3f} | "
            f"{float(lane.get('abstain') or 0.0):.3f} | {int(lane.get('ctx_chars') or 0)} | "
            f"{float(lane.get('context_efficiency_per_1k_chars') or 0.0):.4f} |"
        )

    deltas = dashboard["deltas_vs_provider_federation"]
    lines.extend(
        [
            "",
            "### Dashboard Reading",
            "",
            f"- Token F1 delta vs provider federation: `{deltas['token_f1']:+.3f}`.",
            f"- Abstain delta vs provider federation: `{deltas['abstain']:+.3f}`.",
            f"- Context char delta vs provider federation: `{deltas['context_chars']:+d}`.",
            f"- Context efficiency delta per 1k chars: `{deltas['context_efficiency_per_1k_chars']:+.4f}`.",
            f"- Beats provider federation: `{dashboard['beats_provider_federation']}`.",
            f"- Beats best silo: `{dashboard['beats_best_silo']}`.",
            "",
            "## Scenario Registry",
            "",
            "| Scenario | Prompt | Ontology | Cases | Providers | Errors | Facts | Cross-provider clusters | Promotion | P90 latency s |",
            "|---|---|---|---:|---:|---:|---:|---:|---|---:|",
        ]
    )
    for scenario in payload["scenario_registry"]:
        lines.append(
            f"| `{scenario['scenario_id']}` | `{scenario['prompt_id']}` | `{scenario['ontology_id']}` | "
            f"{scenario['case_count']} | {scenario['provider_count']} | {scenario['error_count']} | "
            f"{scenario['facts']} | {scenario['cross_provider_clusters']} | "
            f"`{scenario['promotion_verdict']}` | {scenario['p90_latency_s']:.2f} |"
        )

    survivorship = payload["survivorship_policy_summary"]
    lines.extend(
        [
            "",
            "## Survivorship Policy Summary",
            "",
            f"- Reviewed fact clusters: `{survivorship['reviewed_fact_clusters']}`.",
            f"- Multi-provider fact clusters: `{survivorship['multi_provider_fact_clusters']}`.",
            f"- Consensus clusters: `{survivorship['consensus_clusters']}`.",
            f"- Conflicting-value clusters: `{survivorship['conflicting_value_clusters']}`.",
            "",
            "| Recommended merge policy |",
            "|---|",
        ]
    )
    for rule in survivorship["recommended_policy"]:
        lines.append(f"| {rule} |")

    lines.extend(
        [
            "",
        "## Category Breakdown",
        "",
        "| Category | Cases | Token F1 | Number overlap | Abstain | Avg context chars |",
        "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for category, row in payload["category_breakdown"].items():
        lines.append(
            f"| {category} | {row['n']} | {row['token_f1']:.3f} | "
            f"{row['number_overlap']:.3f} | {row['abstain']:.3f} | {row['avg_context_chars']} |"
        )

    lines.extend(
        [
            "",
            "## Abstain Taxonomy",
            "",
            "| Reason | Count |",
            "|---|---:|",
        ]
    )
    for reason, count in payload["abstain_taxonomy"]["counts"].items():
        lines.append(f"| `{reason}` | {count} |")

    lines.extend(
        [
            "",
            "## Routing Decision Audit Sample",
            "",
            "| Case | Category | Mode | Providers | Missing slots | Abstain reason | F1 | Answer preview |",
            "|---|---|---|---|---|---|---:|---|",
        ]
    )
    for row in payload["routing_audit"][:30]:
        lines.append(
            f"| `{row['case_id']}` | {row['category']} | `{row['routing_mode']}` | "
            f"{', '.join(row['effective_selected_providers']) or '-'} | "
            f"{'; '.join(row['missing_slots'])[:120] or '-'} | `{row['abstain_reason']}` | "
            f"{row['token_f1']:.3f} | {row['answer_preview'].replace('|', '/')} |"
        )

    lines.extend(
        [
            "",
            "## Fact Cluster Inspector Sample",
            "",
            "| Category | Case | Metric | Providers | Members | Values |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for cluster in payload["fact_clusters"][:30]:
        values = []
        for member in cluster["members"]:
            values.append(
                f"{member['provider_id']}={member['value']}"
                + (f" ({member['period']})" if member.get("period") else "")
            )
        lines.append(
            f"| {cluster['category']} | `{cluster['case_id']}` | "
            f"{cluster['canonical_metric'].replace('|', '/')} | {cluster['provider_count']} | "
            f"{cluster['member_count']} | {'; '.join(values[:5]).replace('|', '/')} |"
        )

    lines.extend(
        [
            "",
            "## Entity Cluster Inspector Sample",
            "",
            "| Category | Canonical entity | Label | Providers | Members | Fact members | Example values |",
            "|---|---|---|---:|---:|---:|---|",
        ]
    )
    for cluster in payload["entity_clusters"][:30]:
        values = []
        for member in cluster["members"]:
            if member.get("value") is not None:
                values.append(
                    f"{member['provider_id']}={member['value']}"
                    + (f" ({member['period']})" if member.get("period") else "")
                )
        lines.append(
            f"| {cluster['category']} | {cluster['canonical_name'].replace('|', '/')} | "
            f"{cluster['primary_label']} | {cluster['provider_count']} | {cluster['member_count']} | "
            f"{cluster['fact_member_count']} | {'; '.join(values[:4]).replace('|', '/') or '-'} |"
        )

    lines.extend(
        [
            "",
            "## Reading",
            "",
            "The routing table shows where the Graph-CoT selector narrowed or preserved provider evidence.",
            "The abstain taxonomy turns failures into governance signals: missing metric/period slots, issuer alias gaps, fact conflicts, or synthesis refusal.",
            "The cluster inspector is not a final golden-record output. It is an MDM review surface showing where provider-specific context should be preserved before merge decisions.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-prefix", default="fedcat-wide-lite-survivorship-v1")
    parser.add_argument("--policy-run-prefix", default="fedcat-category-db-v1")
    parser.add_argument("--baseline-run-prefix", default="fedcat-baseline-80-v1")
    parser.add_argument("--scenario-run-prefix", default="fedcat-wide-lite-v1")
    parser.add_argument("--cluster-limit", type=int, default=80)
    args = parser.parse_args()

    base = ROOT / "outputs" / "evaluation" / "mdm_fedcat"
    out_dir = base / args.run_prefix
    aggregate = _load_json(out_dir / "category_federation_aggregate.json")
    baseline_aggregate = _load_json_if_exists(base / args.baseline_run_prefix / "federation_aggregate.json")
    scenario_aggregate = _load_json_if_exists(base / args.scenario_run_prefix / "scenario_gate_aggregate.json")
    policy = _load_json(base / args.policy_run_prefix / "federation_routing_policy.json")
    records = aggregate["records"]
    routing = _routing_audit(records)
    taxonomy_counts = dict(Counter(row["abstain_reason"] for row in routing))
    scenario_registry = _summarize_scenario_registry(scenario_aggregate)
    fact_clusters = _fact_cluster_inspector(policy, limit=args.cluster_limit)
    entity_clusters = _entity_cluster_inspector(policy, limit=args.cluster_limit)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_prefix": args.run_prefix,
        "policy_run_prefix": args.policy_run_prefix,
        "baseline_run_prefix": args.baseline_run_prefix if baseline_aggregate else "",
        "scenario_run_prefix": args.scenario_run_prefix if scenario_aggregate else "",
        "run_dashboard": _run_dashboard(
            aggregate=aggregate,
            baseline_aggregate=baseline_aggregate,
            scenario_registry=scenario_registry,
            run_prefix=args.run_prefix,
            baseline_run_prefix=args.baseline_run_prefix,
        ),
        "scenario_registry": scenario_registry,
        "category_breakdown": _category_breakdown(records),
        "abstain_taxonomy": {
            "counts": taxonomy_counts,
            "abstain_only_counts": {
                key: value for key, value in taxonomy_counts.items() if key != "answered"
            },
        },
        "routing_audit": routing,
        "fact_clusters": fact_clusters,
        "entity_clusters": entity_clusters,
        "survivorship_policy_summary": _survivorship_policy_summary(fact_clusters),
    }
    (out_dir / "federation_evidence_audit.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_markdown(out_dir / "federation_evidence_audit.md", payload)
    print(f"== wrote {(out_dir / 'federation_evidence_audit.json').relative_to(ROOT)} ==")
    print(f"== wrote {(out_dir / 'federation_evidence_audit.md').relative_to(ROOT)} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
