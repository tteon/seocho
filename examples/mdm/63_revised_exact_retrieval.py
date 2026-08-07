#!/usr/bin/env python3
"""Exact-token evidence arms for blind-validated revised benchmark items."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "revised_exact_retrieval.json"


def main() -> int:
    retrieval = {row["candidate_id"]: row for row in json.loads((BASE / "heldout_exploratory_retrieval.json").read_text())["rows"]}
    revisions = {row["candidate_id"]: row for row in json.loads((BASE / "revised_integrative_candidates.json").read_text())["rows"]}
    validation = json.loads((BASE / "revised_blind_validation.json").read_text())["decisions"]
    routed = {row["candidate_id"]: row for row in json.loads((BASE / "revised_sdcr_routing.json").read_text())["rows"]}
    spec = importlib.util.spec_from_file_location("exact", ROOT / "examples/mdm/53_exact_token_retrieval.py"); assert spec and spec.loader
    exact = importlib.util.module_from_spec(spec); spec.loader.exec_module(exact)
    selector_spec = importlib.util.spec_from_file_location("selector", ROOT / "examples/mdm/51_sdcr_selector_eval.py"); assert selector_spec and selector_spec.loader
    selector = importlib.util.module_from_spec(selector_spec); selector_spec.loader.exec_module(selector)
    index_spec = importlib.util.spec_from_file_location("index", ROOT / "examples/mdm/11_index_providers.py"); assert index_spec and index_spec.loader
    index = importlib.util.module_from_spec(index_spec); index_spec.loader.exec_module(index)
    ids = [cid for cid, value in validation.items() if value["decision"] == "accept"]
    excluded = {case for cid in ids for case in revisions[cid]["source_component_case_ids"]}
    descriptors, idf = selector.prototypes(index.load_cases_full(42), excluded)
    encoder = tiktoken.get_encoding("cl100k_base"); budget = 2048; rows = []
    for cid in ids:
        revision = revisions[cid]; source = retrieval[cid]; left, right = source["evidence"]["left_single"], source["evidence"]["right_single"]
        question = revision["revision"]["revised_question"]; categories = revision["categories"]
        by_category = {categories[0]: left, categories[1]: right}
        scores = selector.category_scores(question, descriptors, idf)
        category_choice = [scores[0][0]]; slot_choices = [category for category, _ in scores[:2]]
        sdcr_choices = routed[cid]["selected_categories"]
        def evidence_for(selected: list[str]):
            views = [by_category[c] for c in selected if c in by_category]
            return views[0] if len(views) == 1 else (exact.alternate(views[0], views[1]) if len(views) >= 2 else [])
        union = {node["id"]: node for node in left + right}
        centralized = sorted(union.values(), key=lambda node: (-len(exact.tokens(question) & exact.tokens(exact.node_text(node))), node["id"]))
        orders = {"left_single": left, "right_single": right, "centralized_single": centralized,
                  "qualified_view_broadcast": exact.alternate(left, right), "category_only": evidence_for(category_choice),
                  "slot_only": evidence_for(slot_choices), "sdcr": evidence_for(sdcr_choices) if routed[cid]["both_required_views_covered"] else []}
        arms = {}
        golds = [revision["revision"]["slot_1_atomic_gold"], revision["revision"]["slot_2_atomic_gold"], revision["revision"]["cross_view_gold"]]
        for arm, order in orders.items():
            evidence, used = exact.cap(order, budget, encoder); coverage = [exact.coverage(evidence, gold) for gold in golds]
            arms[arm] = {"tokens_used": used, "evidence": evidence, "slot_token_recall": sum(x[0] for x in coverage[:2]) / 2,
                         "cross_view_token_recall": coverage[2][0], "routing_success": bool(evidence) if arm == "sdcr" else None}
        rows.append({"candidate_id": cid, "issuer": revision["issuer"], "question": question, "golds": golds,
                     "required_categories": categories, "category_choice": category_choice, "slot_choices": slot_choices,
                     "sdcr_choices": sdcr_choices, "arms": arms})
    arms = list(rows[0]["arms"]); summary = {arm: {"mean_slot_token_recall": sum(r["arms"][arm]["slot_token_recall"] for r in rows) / len(rows),
                                                    "mean_cross_view_token_recall": sum(r["arms"][arm]["cross_view_token_recall"] for r in rows) / len(rows),
                                                    "mean_tokens_used": sum(r["arms"][arm]["tokens_used"] for r in rows) / len(rows)} for arm in arms}
    payload = {"contract": "log2026.revised_exact_retrieval.v1", "cases": len(rows), "evidence_token_budget": budget,
               "tokenizer": "cl100k_base", "sdcr_routing_failure_policy": "empty evidence and ITT zero", "summary": summary, "rows": rows}
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n"); print(json.dumps(summary, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
