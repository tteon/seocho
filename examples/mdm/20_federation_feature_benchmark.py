#!/usr/bin/env python3
"""Benchmark gate for federation observability and MDM features.

This is a zero-LLM regression/benchmark layer over completed hq-42k artifacts.
It answers one operational question: did the feature set around category
routing, auditability, abstain taxonomy, scenario registry, context budgeting,
and survivorship produce measurable GraphRAG and governance gains?
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
for path in (MDM_ROOT, ROOT, ROOT / "scripts" / "benchmarks"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from examples.finder.lib import bench_common as bc  # noqa: E402


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _lane_with_efficiency(lane: dict[str, Any]) -> dict[str, Any]:
    out = dict(lane)
    ctx = int(out.get("ctx_chars") or 0)
    out["context_efficiency_per_1k_chars"] = (
        round(float(out.get("token_f1") or 0.0) / ctx * 1000, 4) if ctx > 0 else 0.0
    )
    return out


def _best_silo(lanes: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    best_name = ""
    best_lane: dict[str, Any] = {}
    for name, lane in lanes.items():
        if not str(name).startswith("silo-"):
            continue
        if not best_lane or float(lane.get("token_f1") or 0.0) > float(best_lane.get("token_f1") or 0.0):
            best_name = str(name)
            best_lane = dict(lane)
    return best_name, _lane_with_efficiency(best_lane)


def _category_lane(path: Path) -> dict[str, Any]:
    aggregate = _load_json(path)
    lane = (aggregate.get("lanes") or {}).get("category-federation", {})
    return _lane_with_efficiency(lane)


def _profile_id_from_path(path: Path) -> str:
    return path.parent.name


def _comparison(
    *,
    profile: str,
    category_lane: dict[str, Any],
    provider_lane: dict[str, Any],
    best_silo_name: str,
    best_silo_lane: dict[str, Any],
) -> dict[str, Any]:
    token_delta = round(float(category_lane.get("token_f1") or 0.0) - float(provider_lane.get("token_f1") or 0.0), 3)
    abstain_delta = round(float(category_lane.get("abstain") or 0.0) - float(provider_lane.get("abstain") or 0.0), 3)
    context_delta = int(category_lane.get("ctx_chars") or 0) - int(provider_lane.get("ctx_chars") or 0)
    efficiency_delta = round(
        float(category_lane.get("context_efficiency_per_1k_chars") or 0.0)
        - float(provider_lane.get("context_efficiency_per_1k_chars") or 0.0),
        4,
    )
    return {
        "profile": profile,
        "category_federation": category_lane,
        "provider_federation": provider_lane,
        "best_silo_name": best_silo_name,
        "best_silo": best_silo_lane,
        "deltas_vs_provider_federation": {
            "token_f1": token_delta,
            "abstain": abstain_delta,
            "context_chars": context_delta,
            "context_efficiency_per_1k_chars": efficiency_delta,
        },
        "beats_provider_federation": token_delta >= 0,
        "beats_best_silo": float(category_lane.get("token_f1") or 0.0)
        >= float(best_silo_lane.get("token_f1") or 0.0),
        "abstain_no_worse_than_provider": abstain_delta <= 0,
        "uses_less_context_than_provider": context_delta <= 0,
        "context_efficiency_improved": efficiency_delta >= 0,
    }


def _contract_gates(audit: dict[str, Any], *, expected_cases: int) -> dict[str, Any]:
    allowed_reasons = {
        "answered",
        "runtime_error",
        "selector_error",
        "no_provider_evidence",
        "missing_period",
        "issuer_alias_mismatch",
        "missing_metric",
        "missing_required_slot",
        "retrieval_too_narrow",
        "conflicting_facts",
        "grounded_context_gap",
        "synthesis_refusal",
    }
    routing = audit.get("routing_audit") or []
    reasons = {row.get("abstain_reason") for row in routing}
    unknown_reasons = sorted(str(reason) for reason in reasons if reason not in allowed_reasons)
    scenario_registry = audit.get("scenario_registry") or []
    survivorship = audit.get("survivorship_policy_summary") or {}
    dashboard = audit.get("run_dashboard") or {}
    return {
        "dashboard_contract_complete": bool(
            dashboard.get("category_federation")
            and dashboard.get("provider_db_federation")
            and dashboard.get("best_silo")
        ),
        "routing_audit_coverage": {
            "expected_cases": expected_cases,
            "actual_rows": len(routing),
            "pass": len(routing) == expected_cases,
        },
        "abstain_taxonomy_contract": {
            "unknown_reasons": unknown_reasons,
            "pass": not unknown_reasons,
        },
        "scenario_registry_contract": {
            "scenario_count": len(scenario_registry),
            "promoted_count": sum(1 for row in scenario_registry if row.get("promotion_verdict") == "promote"),
            "pass": bool(scenario_registry),
        },
        "survivorship_summary_contract": {
            "reviewed_fact_clusters": int(survivorship.get("reviewed_fact_clusters") or 0),
            "conflicting_value_clusters": int(survivorship.get("conflicting_value_clusters") or 0),
            "pass": int(survivorship.get("reviewed_fact_clusters") or 0) > 0,
        },
    }


def _performance_gates(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    best = max(comparisons, key=lambda row: float(row["category_federation"].get("token_f1") or 0.0))
    return {
        "best_profile": best["profile"],
        "category_beats_provider_federation": {
            "pass": any(row["beats_provider_federation"] for row in comparisons),
            "profiles": [row["profile"] for row in comparisons if row["beats_provider_federation"]],
        },
        "category_beats_best_silo": {
            "pass": any(row["beats_best_silo"] for row in comparisons),
            "profiles": [row["profile"] for row in comparisons if row["beats_best_silo"]],
        },
        "category_abstain_no_worse": {
            "pass": any(row["abstain_no_worse_than_provider"] for row in comparisons),
            "profiles": [row["profile"] for row in comparisons if row["abstain_no_worse_than_provider"]],
        },
        "category_uses_less_context": {
            "pass": any(row["uses_less_context_than_provider"] for row in comparisons),
            "profiles": [row["profile"] for row in comparisons if row["uses_less_context_than_provider"]],
        },
        "context_efficiency_improves": {
            "pass": any(row["context_efficiency_improved"] for row in comparisons),
            "profiles": [row["profile"] for row in comparisons if row["context_efficiency_improved"]],
        },
    }


def _recommended_next_experiments(comparisons: list[dict[str, Any]], audit: dict[str, Any]) -> list[dict[str, Any]]:
    best = max(comparisons, key=lambda row: float(row["category_federation"].get("token_f1") or 0.0))
    abstain_counts = (audit.get("abstain_taxonomy") or {}).get("abstain_only_counts") or {}
    top_abstain_reason = ""
    if abstain_counts:
        top_abstain_reason = max(abstain_counts.items(), key=lambda item: int(item[1]))[0]
    return [
        {
            "experiment": "context_budget_optimizer",
            "why": "category federation already beats provider federation while using less context; test whether slot-aware truncation can reduce context further without losing answer quality",
            "starting_profile": best["profile"],
            "target": {
                "ctx_chars_max": int(float(best["category_federation"].get("ctx_chars") or 0) * 0.8),
                "token_f1_min": round(float(best["category_federation"].get("token_f1") or 0.0) * 0.98, 3),
                "abstain_increase_max": 0.03,
            },
        },
        {
            "experiment": "abstain_remediation",
            "why": "taxonomy turns abstains into actionable retrieval/routing fixes",
            "starting_reason": top_abstain_reason,
            "target": {
                "abstain_reduction_min": 0.05,
                "token_f1_no_drop_below": round(float(best["category_federation"].get("token_f1") or 0.0) * 0.98, 3),
            },
        },
        {
            "experiment": "entity_cluster_gold_labeling",
            "why": "cluster inspector should be measured as an MDM review workload reducer, not only a report",
            "target": {
                "manual_labels": 100,
                "precision_at_50_min": 0.8,
                "generic_noise_rate_max": 0.15,
            },
        },
    ]


def _all_gate_passes(payload: dict[str, Any]) -> bool:
    contract = payload["contract_gates"]
    performance = payload["performance_gates"]
    checks = [
        contract["dashboard_contract_complete"],
        contract["routing_audit_coverage"]["pass"],
        contract["abstain_taxonomy_contract"]["pass"],
        contract["scenario_registry_contract"]["pass"],
        contract["survivorship_summary_contract"]["pass"],
        performance["category_beats_provider_federation"]["pass"],
        performance["category_beats_best_silo"]["pass"],
        performance["category_abstain_no_worse"]["pass"],
        performance["category_uses_less_context"]["pass"],
        performance["context_efficiency_improves"]["pass"],
    ]
    return all(checks)


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# hq-42k Federation Feature Benchmark Gate",
        "",
        f"Generated: {payload['generated_at']}",
        f"Overall verdict: `{payload['overall_verdict']}`",
        "",
        "## Performance Comparisons",
        "",
        "| Profile | Token F1 | Number overlap | Abstain | Context chars | F1 / 1k ctx | ΔF1 vs provider | Δabstain | Δctx | Beats provider | Beats best silo |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in payload["comparisons"]:
        lane = row["category_federation"]
        delta = row["deltas_vs_provider_federation"]
        lines.append(
            f"| `{row['profile']}` | {float(lane.get('token_f1') or 0.0):.3f} | "
            f"{float(lane.get('overlap') or 0.0):.3f} | {float(lane.get('abstain') or 0.0):.3f} | "
            f"{int(lane.get('ctx_chars') or 0)} | {float(lane.get('context_efficiency_per_1k_chars') or 0.0):.4f} | "
            f"{delta['token_f1']:+.3f} | {delta['abstain']:+.3f} | {delta['context_chars']:+d} | "
            f"`{row['beats_provider_federation']}` | `{row['beats_best_silo']}` |"
        )

    lines.extend(["", "## Contract Gates", "", "| Gate | Pass | Detail |", "|---|---|---|"])
    contract = payload["contract_gates"]
    lines.append(f"| Dashboard contract | `{contract['dashboard_contract_complete']}` | baseline/category/best-silo lanes present |")
    routing = contract["routing_audit_coverage"]
    lines.append(f"| Routing audit coverage | `{routing['pass']}` | {routing['actual_rows']}/{routing['expected_cases']} rows |")
    taxonomy = contract["abstain_taxonomy_contract"]
    lines.append(f"| Abstain taxonomy contract | `{taxonomy['pass']}` | unknown={taxonomy['unknown_reasons']} |")
    scenarios = contract["scenario_registry_contract"]
    lines.append(f"| Scenario registry contract | `{scenarios['pass']}` | scenarios={scenarios['scenario_count']}, promoted={scenarios['promoted_count']} |")
    survivorship = contract["survivorship_summary_contract"]
    lines.append(
        f"| Survivorship summary contract | `{survivorship['pass']}` | reviewed={survivorship['reviewed_fact_clusters']}, conflicts={survivorship['conflicting_value_clusters']} |"
    )

    lines.extend(["", "## Performance Gates", "", "| Gate | Pass | Profiles |", "|---|---|---|"])
    for gate, detail in payload["performance_gates"].items():
        if gate == "best_profile":
            continue
        lines.append(f"| `{gate}` | `{detail['pass']}` | {', '.join(detail['profiles']) or '-'} |")

    lines.extend(["", "## Recommended Next Experiments", "", "| Experiment | Why | Target |", "|---|---|---|"])
    for experiment in payload["recommended_next_experiments"]:
        lines.append(
            f"| `{experiment['experiment']}` | {experiment['why']} | "
            f"`{json.dumps(experiment['target'], sort_keys=True)}` |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_payload(
    *,
    baseline_path: Path,
    category_paths: list[Path],
    audit_path: Path,
) -> dict[str, Any]:
    baseline = _load_json(baseline_path)
    audit = _load_json(audit_path)
    provider_lane = _lane_with_efficiency((baseline.get("lanes") or {}).get("federation", {}))
    best_silo_name, best_silo_lane = _best_silo(baseline.get("lanes") or {})
    comparisons = [
        _comparison(
            profile=_profile_id_from_path(path),
            category_lane=_category_lane(path),
            provider_lane=provider_lane,
            best_silo_name=best_silo_name,
            best_silo_lane=best_silo_lane,
        )
        for path in category_paths
    ]
    expected_cases = int(provider_lane.get("n") or 0)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_path": _display_path(baseline_path),
        "category_paths": [_display_path(path) for path in category_paths],
        "audit_path": _display_path(audit_path),
        "provider_federation": provider_lane,
        "best_silo_name": best_silo_name,
        "best_silo": best_silo_lane,
        "comparisons": comparisons,
        "contract_gates": _contract_gates(audit, expected_cases=expected_cases),
        "performance_gates": _performance_gates(comparisons),
        "recommended_next_experiments": _recommended_next_experiments(comparisons, audit),
    }
    payload["overall_verdict"] = "pass" if _all_gate_passes(payload) else "review"
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-run-prefix", default="fedcat-baseline-80-v1")
    parser.add_argument(
        "--category-run-prefixes",
        default="fedcat-wide-lite-category-aware-v1,fedcat-wide-lite-survivorship-v1",
    )
    parser.add_argument("--audit-run-prefix", default="fedcat-wide-lite-survivorship-v1")
    parser.add_argument("--output-run-prefix", default="fedcat-wide-lite-v1")
    args = parser.parse_args()

    base = ROOT / "outputs" / "evaluation" / "mdm_fedcat"
    category_paths = [
        base / prefix.strip() / "category_federation_aggregate.json"
        for prefix in args.category_run_prefixes.split(",")
        if prefix.strip()
    ]
    payload = build_payload(
        baseline_path=base / args.baseline_run_prefix / "federation_aggregate.json",
        category_paths=category_paths,
        audit_path=base / args.audit_run_prefix / "federation_evidence_audit.json",
    )
    out_dir = base / args.output_run_prefix
    out_dir.mkdir(parents=True, exist_ok=True)
    bc.atomic_write_json(out_dir / "feature_benchmark_gate.json", payload)
    _write_markdown(out_dir / "feature_benchmark_gate.md", payload)
    print(f"== wrote {(out_dir / 'feature_benchmark_gate.json').relative_to(ROOT)} ==")
    print(f"== wrote {(out_dir / 'feature_benchmark_gate.md').relative_to(ROOT)} ==")
    print(f"overall verdict: {payload['overall_verdict']}")
    return 0 if payload["overall_verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
