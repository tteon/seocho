#!/usr/bin/env python3
"""Slot, numeric, and unsupported-claim evaluation with issuer-clustered CIs."""
from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "heldout_grounding_evaluation.json"
STOP = {"the", "and", "for", "from", "that", "this", "with", "does", "not", "provided", "evidence"}


def words(value: Any) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", str(value).lower()) if len(t) > 2 and t not in STOP}


def numbers(value: Any) -> set[str]:
    return {n.replace(",", "").replace("$", "").rstrip("%") for n in re.findall(r"\$?-?\d[\d,]*(?:\.\d+)?%?", str(value))}


def f1(answer: str, gold: str) -> float:
    a, g = words(answer), words(gold)
    if not a or not g: return 0.0
    overlap = len(a & g); precision = overlap / len(a); recall = overlap / len(g)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def evidence_text(nodes: list[dict[str, Any]]) -> str:
    return " ".join(" ".join([*node.get("labels", []), *(str(v) for v in node.get("props", {}).values())]) for node in nodes)


def percentile(values: list[float], p: float) -> float:
    return sorted(values)[min(int(p * len(values)), len(values) - 1)]


def clustered_delta(rows: list[dict[str, Any]], field: str, iterations: int = 10000) -> tuple[float, list[float]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows: grouped[row["issuer"]].append(row)
    issuers = sorted(grouped); rng = random.Random(20260712); deltas = []
    for _ in range(iterations):
        sample = rng.choices(issuers, k=len(issuers))
        sampled = [row for issuer in sample for row in grouped[issuer]]
        coalition = mean(row["arms"]["sdcr_coalition"][field] for row in sampled)
        left = mean(row["arms"]["left_single"][field] for row in sampled)
        right = mean(row["arms"]["right_single"][field] for row in sampled)
        deltas.append(coalition - max(left, right))
    point = mean(row["arms"]["sdcr_coalition"][field] for row in rows) - max(
        mean(row["arms"]["left_single"][field] for row in rows), mean(row["arms"]["right_single"][field] for row in rows))
    return point, [percentile(deltas, .025), percentile(deltas, .975)]


def main() -> int:
    answers = json.loads((BASE / "heldout_exploratory_answers.json").read_text())["rows"]
    retrieval = json.loads((BASE / "heldout_exploratory_retrieval.json").read_text())["rows"]
    candidates = json.loads((BASE / "candidates.json").read_text())["candidates"]
    by_answer = {(row["candidate_id"], row["arm"]): row for row in answers}
    by_candidate = {row["candidate_id"]: row for row in candidates}
    cases = []
    for retrieved in retrieval:
        cid = retrieved["candidate_id"]; candidate = by_candidate[cid]; arm_rows = {}
        for arm in ("left_single", "right_single", "sdcr_coalition"):
            output = by_answer[(cid, arm)]; response = output.get("response") if isinstance(output.get("response"), dict) else {}
            answer = str(response.get("answer", "")); nodes = retrieved["evidence"][arm]; text = evidence_text(nodes)
            valid_ids = {f"E{i + 1}" for i in range(len(nodes))}; used = set(response.get("used_evidence_ids") or [])
            declared = [bool(response.get("slot_1_supported")), bool(response.get("slot_2_supported"))]
            per_slot = []
            for index, gold in enumerate(candidate["required_gold_slots"]):
                gn = numbers(gold)
                per_slot.append({"token_f1": f1(answer, gold),
                                 "numeric_recall": len(gn & numbers(answer)) / len(gn) if gn else None,
                                 "declared_supported": declared[index]})
            numeric_claim_text = re.sub(r"\bE\d+\b", "", answer, flags=re.IGNORECASE)
            answer_numbers = numbers(numeric_claim_text); evidence_numbers = numbers(text)
            arm_rows[arm] = {
                "slot_macro_f1": mean(slot["token_f1"] for slot in per_slot),
                "slot_numeric_recall": mean(slot["numeric_recall"] for slot in per_slot if slot["numeric_recall"] is not None) if any(slot["numeric_recall"] is not None for slot in per_slot) else 0.0,
                "unsupported_numeric_rate": len(answer_numbers - evidence_numbers) / len(answer_numbers) if answer_numbers else 0.0,
                "invalid_citation_rate": len(used - valid_ids) / len(used) if used else 0.0,
                "unsupported_support_declaration_rate": sum(declared) / 2 if any(declared) and not (used & valid_ids) else 0.0,
                "schema_valid": bool(response), "per_slot": per_slot,
            }
        cases.append({"candidate_id": cid, "issuer": candidate["issuer"], "arms": arm_rows})
    fields = ["slot_macro_f1", "slot_numeric_recall", "unsupported_numeric_rate", "invalid_citation_rate", "unsupported_support_declaration_rate"]
    summary = {arm: {field: round(mean(row["arms"][arm][field] for row in cases), 6) for field in fields}
               for arm in ("left_single", "right_single", "sdcr_coalition")}
    comparisons = {}
    for field in fields:
        point, ci = clustered_delta(cases, field)
        comparisons[field] = {"coalition_minus_best_fixed": round(point, 6), "issuer_clustered_bootstrap_95_ci": [round(ci[0], 6), round(ci[1], 6)]}
    payload = {"contract": "log2026.answer_grounding_evaluation.v1", "cases": len(cases), "issuer_clusters": len({r['issuer'] for r in cases}),
               "unsupported_claim_scope": "deterministic numeric grounding after removing E<number> citation tokens, citation validity, and unsupported support declarations; not semantic NLI",
               "summary": summary, "comparisons": comparisons, "rows": cases}
    OUT.write_text(json.dumps(payload, indent=2) + "\n"); print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
