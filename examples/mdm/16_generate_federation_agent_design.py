#!/usr/bin/env python3
"""Generate federation-agent routing design artifacts for hq-42k.

Inputs:
  - category entity/fact census
  - QA capability benchmark
  - category database projection artifact

Outputs:
  - deterministic routing policy over category databases and provider evidence
  - prompt/ontology scenario matrix
  - Graph-CoT selector prompt for MiniMax-M2.7
  - optional MiniMax-M2.7 routing review JSON
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MDM_ROOT = Path(__file__).resolve().parent
ROOT = MDM_ROOT.parents[1]
for path in (MDM_ROOT, ROOT, ROOT / "examples" / "finder" / "lib"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dotenv import dotenv_values  # noqa: E402

for key, value in dotenv_values(ROOT / ".env").items():
    if value is not None:
        os.environ.setdefault(key, value)


PROMPT_VARIANTS = [
    {
        "prompt_id": "neutral_kg@v1",
        "status": "executed_baseline",
        "intent": "vendor-neutral extraction; current hq-42k baseline",
    },
    {
        "prompt_id": "fibo_strict_entity_first@v1",
        "status": "planned",
        "intent": "favor FIBO business entities and suppress document/boilerplate entities",
    },
    {
        "prompt_id": "category_aware_fact_first@v1",
        "status": "planned",
        "intent": "category-specific extraction with stronger metric/fact typing",
    },
    {
        "prompt_id": "duplicate_aware_survivorship@v1",
        "status": "planned",
        "intent": "extract aliases, identifiers, and evidence needed for later SAME_AS and survivorship",
    },
]

ONTOLOGY_VARIANTS = [
    {
        "ontology_id": "generic_baseline",
        "modules": [],
        "status": "planned_control",
        "intent": "measure prompt/model behavior without FIBO structure",
    },
    {
        "ontology_id": "fibo_medium_current",
        "modules": ["be", "ind", "fbc", "dbt", "acc"],
        "status": "executed_baseline",
        "intent": "current hq-42k medium FIBO arm",
    },
    {
        "ontology_id": "fibo_finance_core",
        "modules": ["be", "fbc", "fnd", "ind", "acc"],
        "status": "planned",
        "intent": "finance/reference-data core with fewer market/security details",
    },
    {
        "ontology_id": "fibo_capital_markets",
        "modules": ["be", "fbc", "sec", "mkt", "corp"],
        "status": "planned",
        "intent": "security/market/category-heavy extraction arm",
    },
    {
        "ontology_id": "fibo_full_local",
        "modules": ["be", "fbc", "sec", "fnd", "ind", "dbt", "mkt", "acc", "corp"],
        "status": "planned_expensive",
        "intent": "maximal local FIBO slice; use only after profile gate",
    },
]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _category_database_map(projection: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {
        row["category"]: {
            "database": row["database"],
            "uri": row["uri"],
            "slug": row["slug"],
        }
        for row in projection["results"]
    }


def _best_provider_by_category(benchmark: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    matrix = benchmark["capability_matrix"]
    for category in benchmark["categories"]:
        vals = {
            provider: score
            for provider, by_category in matrix.items()
            for cat, score in by_category.items()
            if cat == category and score is not None
        }
        best = max(vals, key=vals.get)
        out[category] = {
            "best_provider": best,
            "best_token_f1": vals[best],
            "all_provider_token_f1": vals,
        }
    return out


def _routing_mode(census_row: dict[str, Any], best: dict[str, Any]) -> str:
    dup = census_row["duplication_ratio"]
    cross = census_row["cross_model_cluster_rate"]
    conflicts = census_row["fact_conflict_groups"]
    facts = census_row["raw_facts"]
    if conflicts:
        return "category_db_survivorship_first"
    if dup >= 0.30 or cross >= 0.30:
        return "category_db_multi_provider_context"
    if facts >= 50:
        return "category_db_best_provider_then_fact_union"
    if best["best_token_f1"] >= 0.25:
        return "category_db_best_provider_primary"
    return "category_db_broadcast_fallback"


def _mode_contract(mode: str) -> dict[str, Any]:
    contracts = {
        "category_db_survivorship_first": {
            "provider_selection": "read all providers with fact candidates in the category database",
            "evidence_order": ["fact_inventory", "survivorship_or_quarantine", "entity_cluster_context"],
            "synthesis_rule": "answer only after exposing all source values and the survivorship rule",
        },
        "category_db_multi_provider_context": {
            "provider_selection": "read all providers participating in duplicate/cross-model entity clusters; rank by provider score for synthesis",
            "evidence_order": ["entity_clusters", "provider_contexts", "missing_provider_slots"],
            "synthesis_rule": "merge overlapping entity context and preserve provider-specific disagreements",
        },
        "category_db_best_provider_then_fact_union": {
            "provider_selection": "start with primary_provider; add other providers only for missing facts or conflicting values",
            "evidence_order": ["primary_provider_context", "fact_union", "coverage_gap"],
            "synthesis_rule": "use best provider for prose and union facts for numeric slots",
        },
        "category_db_best_provider_primary": {
            "provider_selection": "start with primary_provider; fallback to broadcast only if required slots are missing",
            "evidence_order": ["primary_provider_context", "fallback_provider_contexts"],
            "synthesis_rule": "keep source provider explicit in final answer",
        },
        "category_db_broadcast_fallback": {
            "provider_selection": "broadcast to all providers because no strong duplicate or provider-quality signal dominates",
            "evidence_order": ["all_provider_contexts", "abstention_report"],
            "synthesis_rule": "prefer conservative synthesis; abstain if providers do not ground required slots",
        },
    }
    return contracts[mode]


def _prompt_ontology_strategy(category: str, row: dict[str, Any]) -> dict[str, Any]:
    facts = row["raw_facts"]
    dup = row["duplication_ratio"]
    cross = row["cross_model_cluster_rate"]
    conflicts = row["fact_conflict_groups"]
    if conflicts:
        prompt = "duplicate_aware_survivorship@v1"
    elif facts >= 50:
        prompt = "category_aware_fact_first@v1"
    elif dup >= 0.30 or cross >= 0.30:
        prompt = "fibo_strict_entity_first@v1"
    else:
        prompt = "neutral_kg@v1"

    if category in {"Financials", "Footnotes", "Accounting", "Shareholder return"}:
        ontology = "fibo_finance_core"
    elif category in {"Company overview", "Governance", "Legal", "Risk"}:
        ontology = "fibo_capital_markets"
    else:
        ontology = "fibo_medium_current"
    return {
        "recommended_prompt_id": prompt,
        "recommended_ontology_id": ontology,
        "selection_basis": {
            "raw_facts": facts,
            "duplicate_ratio": dup,
            "cross_model_cluster_rate": cross,
            "fact_conflict_groups": conflicts,
        },
    }


def _build_routing_policy(census: dict[str, Any], benchmark: dict[str, Any], projection: dict[str, Any]) -> dict[str, Any]:
    db_map = _category_database_map(projection)
    best_by_cat = _best_provider_by_category(benchmark)
    categories = {}
    for category, row in census["category_census"].items():
        best = best_by_cat[category]
        mode = _routing_mode(row, best)
        categories[category] = {
            "category_database": db_map[category],
            "routing_mode": mode,
            "primary_provider": best["best_provider"],
            "provider_scores": best["all_provider_token_f1"],
            "duplicate_ratio": row["duplication_ratio"],
            "cross_model_cluster_rate": row["cross_model_cluster_rate"],
            "fact_conflict_groups": row["fact_conflict_groups"],
            "raw_facts": row["raw_facts"],
            "provider_inventory": row["by_provider"],
            "prompt_ontology_strategy": _prompt_ontology_strategy(category, row),
            "routing_criteria": {
                "survivorship_first": "fact_conflict_groups > 0",
                "multi_provider_context": "duplication_ratio >= 0.30 or cross_model_cluster_rate >= 0.30",
                "best_provider_then_fact_union": "raw_facts >= 50 and no current fact conflicts",
                "best_provider_primary": "best_provider token_f1 >= 0.25 when no stronger evidence-topology rule applies",
                "broadcast_fallback": "no strong provider or overlap signal; preserve coverage and abstention diagnostics",
            },
            "selection_contract": _mode_contract(mode),
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topology": "single_dbms_category_databases",
        "reasoning_model_default": {
            "llm": "mara/MiniMax-M2.7",
            "role": "Graph-CoT QuerySupervisorAgent / category+provider selector",
        },
        "answer_model_default": {
            "llm": "mara/MiniMax-M2.7",
            "role": "grounded synthesis over selected evidence bundle",
        },
        "provider_model_keys": {
            "deepseek": "DeepSeek-V3.1",
            "gptoss": "gpt-oss-120b",
            "minimax25": "MiniMax-M2.5",
            "minimax27": "MiniMax-M2.7",
        },
        "categories": categories,
    }


def _build_experiment_matrix(index_meta: dict[str, Any]) -> dict[str, Any]:
    scenarios = []
    for prompt in PROMPT_VARIANTS:
        for ontology in ONTOLOGY_VARIANTS:
            executed = (
                prompt["prompt_id"] == index_meta.get("prompt_id")
                and ontology["modules"] == index_meta.get("ontology_modules")
            )
            scenarios.append(
                {
                    "scenario_id": f"{prompt['prompt_id']}__{ontology['ontology_id']}",
                    "prompt": prompt,
                    "ontology": ontology,
                    "models": ["DeepSeek-V3.1", "gpt-oss-120b", "MiniMax-M2.5", "MiniMax-M2.7"],
                    "categories": index_meta.get("categories", []),
                    "execution_status": "executed" if executed else "planned",
                    "cost_gate": "do_not_run_full_cross_product_without_census_delta_gate"
                    if not executed
                    else "complete",
                    "recommended_sampling": (
                        "full current 8 categories x 2 cases only after prompt/ontology "
                        "delta beats baseline on entity duplicate precision or fact recall"
                    ),
                }
            )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_baseline": {
            "prompt_id": index_meta.get("prompt_id"),
            "prompt_hash": index_meta.get("prompt_hash"),
            "ontology_modules": index_meta.get("ontology_modules"),
            "ontology_hash": index_meta.get("ontology_hash"),
        },
        "prompt_variants": PROMPT_VARIANTS,
        "ontology_variants": ONTOLOGY_VARIANTS,
        "scenarios": scenarios,
    }


def _selector_prompt(policy: dict[str, Any]) -> str:
    compact = {
        category: {
            "database": row["category_database"]["database"],
            "mode": row["routing_mode"],
            "primary_provider": row["primary_provider"],
            "duplicate_ratio": row["duplicate_ratio"],
            "cross_model_cluster_rate": row["cross_model_cluster_rate"],
            "fact_conflict_groups": row["fact_conflict_groups"],
            "provider_scores": row["provider_scores"],
            "prompt_ontology_strategy": row["prompt_ontology_strategy"],
        }
        for category, row in policy["categories"].items()
    }
    return (
        "# Graph-CoT Federation Selector Prompt\n\n"
        "Model: mara/MiniMax-M2.7\n\n"
        "You are the QuerySupervisorAgent for a category-centric data federation graph.\n"
        "Select the category database first, then select provider/model evidence.\n"
        "Use only the provided routing policy and preserve abstention/quarantine when evidence is insufficient.\n\n"
        "Return JSON with keys: category, database, routing_mode, selected_providers, required_evidence, "
        "survivorship_required, missing_slots, rationale.\n\n"
        "Routing policy:\n\n"
        "```json\n"
        + json.dumps(compact, indent=2, sort_keys=True)
        + "\n```\n\n"
        "Decision rules:\n"
        "1. High duplicate/cross-model overlap: gather multi-provider entity clusters.\n"
        "2. Fact conflicts: run survivorship/quarantine before final answer.\n"
        "3. Low overlap but strong provider score: use primary provider, then fallback to federation if slots are missing.\n"
        "4. Never answer from a provider/model that lacks the required category/database evidence.\n"
    )


def _llm_review(policy: dict[str, Any], matrix: dict[str, Any]) -> dict[str, Any]:
    from examples.finder.lib import llm_io

    spec = llm_io.parse_llm_spec("mara/MiniMax-M2.7")
    client = llm_io.make_chat_client(spec)
    system = (
        "You are a graph federation architecture reviewer. Return compact JSON only. "
        "Evaluate whether the routing policy uses category databases, provider/model provenance, "
        "and prompt/ontology variants coherently."
    )
    user = json.dumps(
        {
            "routing_policy": policy,
            "experiment_matrix_summary": {
                "prompt_variants": matrix["prompt_variants"],
                "ontology_variants": matrix["ontology_variants"],
                "scenario_count": len(matrix["scenarios"]),
            },
        },
        indent=2,
        sort_keys=True,
    )
    text = llm_io.chat_complete(
        client=client,
        model=spec.model,
        system=system,
        user=user,
        temperature=0.1,
        response_format={"type": "json_object"},
        label="fedcat-graph-cot-review",
        max_attempts=3,
        spec=spec,
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw_text": text, "parse_error": "json_decode_failed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-prefix", default="fedcat-single-dbms-v1")
    parser.add_argument("--category-run-prefix", default="fedcat-category-db-v1")
    parser.add_argument("--index-run-prefix", default="fedcat-v1")
    parser.add_argument("--run-llm-review", action="store_true")
    args = parser.parse_args()

    base = ROOT / "outputs" / "evaluation" / "mdm_fedcat"
    census = _load_json(base / args.run_prefix / "category_entity_census.json")
    benchmark = _load_json(base / args.run_prefix / "federation_aggregate.json")
    projection = _load_json(base / args.category_run_prefix / "category_projection_artifact.json")
    index_meta = _load_json(base / args.index_run_prefix / "index_aggregate.json")

    policy = _build_routing_policy(census, benchmark, projection)
    matrix = _build_experiment_matrix(index_meta)
    prompt = _selector_prompt(policy)

    out_dir = base / args.category_run_prefix
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "federation_routing_policy.json").write_text(
        json.dumps(policy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "prompt_ontology_experiment_matrix.json").write_text(
        json.dumps(matrix, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "graph_cot_selector_prompt.md").write_text(prompt, encoding="utf-8")

    if args.run_llm_review:
        review = _llm_review(policy, matrix)
        (out_dir / "minimax27_graph_cot_policy_review.json").write_text(
            json.dumps(review, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print("== wrote MiniMax-M2.7 Graph-CoT policy review ==")

    print(f"== wrote design artifacts under {out_dir.relative_to(ROOT)} ==")
    print(f"categories: {len(policy['categories'])}; scenarios: {len(matrix['scenarios'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
