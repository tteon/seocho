#!/usr/bin/env python3
"""Exact serialized-token retrieval sensitivity and routing baselines."""
from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import mean
from typing import Any

import tiktoken

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
CROSS = BASE / "log2026-full-finder-cross-view-v1"
SELECTOR = BASE / "log2026-sdcr-selector-eval-v1/evaluation.json"
OUT = CROSS / "exact_token_retrieval.json"
STOP = {"and", "for", "from", "that", "the", "this", "with", "impact", "what"}


def tokens(value: Any) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", str(value).lower()) if len(t) >= 3 and t not in STOP}


def numbers(value: Any) -> set[str]:
    return {n.replace(",", "").replace("$", "").rstrip("%") for n in re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?", str(value))}


def node_text(node: dict[str, Any]) -> str:
    return " ".join([*node.get("labels", []), *(str(v) for v in node.get("props", {}).values())])


def alternate(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for index in range(max(len(left), len(right))):
        if index < len(left): result.append(left[index])
        if index < len(right): result.append(right[index])
    return result


def cap(nodes: list[dict[str, Any]], budget: int, encoder: Any) -> tuple[list[dict[str, Any]], int]:
    selected = []; used = 0
    for index, node in enumerate(nodes):
        unit = f"E{index + 1}:" + json.dumps(node, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        cost = len(encoder.encode(unit))
        if used + cost > budget: break
        selected.append(node); used += cost
    return selected, used


def coverage(nodes: list[dict[str, Any]], gold: str) -> tuple[float, float | None]:
    text = " ".join(node_text(node) for node in nodes); gt = tokens(gold); gn = numbers(gold)
    return (len(gt & tokens(text)) / len(gt) if gt else 0.0,
            len(gn & numbers(text)) / len(gn) if gn else None)


def main() -> int:
    retrieval = json.loads((CROSS / "heldout_exploratory_retrieval.json").read_text())["rows"]
    candidates = {row["candidate_id"]: row for row in json.loads((CROSS / "candidates.json").read_text())["candidates"]}
    receipts = {(row["query_id"], row["policy"]): row for row in json.loads(SELECTOR.read_text())["decision_receipts"]}
    encoder = tiktoken.get_encoding("cl100k_base"); budgets = [512, 1024, 2048, 4096]
    rows = []
    for item in retrieval:
        cid = item["candidate_id"]; candidate = candidates[cid]; left = item["evidence"]["left_single"]; right = item["evidence"]["right_single"]
        question = " ".join(candidate["component_questions"]); union = {node["id"]: node for node in left + right}
        centralized = sorted(union.values(), key=lambda node: (-len(tokens(question) & tokens(node_text(node))), node["id"]))
        view_by_category = {category: nodes for category, nodes in zip(candidate["required_categories"], (left, right))}
        qid = "cross-" + cid
        arm_orders = {"left_single": left, "right_single": right, "centralized_single": centralized,
                      "qualified_view_broadcast": alternate(left, right)}
        for policy in ("category_only", "slot_only", "sdcr"):
            selected_categories = receipts[(qid, policy)]["selected_agents"]
            selected_views = [view_by_category[category] for category in selected_categories if category in view_by_category]
            arm_orders[policy] = selected_views[0] if len(selected_views) == 1 else (alternate(selected_views[0], selected_views[1]) if len(selected_views) >= 2 else [])
        for budget in budgets:
            arms = {}
            for arm, order in arm_orders.items():
                evidence, used = cap(order, budget, encoder); slots = [coverage(evidence, gold) for gold in candidate["required_gold_slots"]]
                arms[arm] = {"tokens_used": used, "nodes": len(evidence), "slot_token_recall": mean(x[0] for x in slots),
                             "slot_numeric_recall": mean(x[1] for x in slots if x[1] is not None) if any(x[1] is not None for x in slots) else None}
            rows.append({"candidate_id": cid, "issuer": candidate["issuer"], "budget": budget, "arms": arms})
    arms = list(rows[0]["arms"]); summary = {}
    for budget in budgets:
        subset = [row for row in rows if row["budget"] == budget]
        summary[str(budget)] = {arm: {"mean_tokens_used": round(mean(r["arms"][arm]["tokens_used"] for r in subset), 3),
                                      "mean_nodes": round(mean(r["arms"][arm]["nodes"] for r in subset), 3),
                                      "slot_token_recall": round(mean(r["arms"][arm]["slot_token_recall"] for r in subset), 6),
                                      "slot_numeric_recall": round(mean(r["arms"][arm]["slot_numeric_recall"] for r in subset if r["arms"][arm]["slot_numeric_recall"] is not None), 6)} for arm in arms}
    payload = {"contract": "log2026.exact_token_retrieval.v1", "tokenizer": "tiktoken cl100k_base",
               "serialization": "E<number>:canonical compact sorted-key JSON plus newline; units never truncated",
               "budgets": budgets, "broadcast_scope": "two evidence-qualified category views; full eight-agent cost is evaluated in routing",
               "summary": summary, "rows": rows}
    OUT.write_text(json.dumps(payload, indent=2) + "\n"); print(OUT); return 0


if __name__ == "__main__":
    raise SystemExit(main())
