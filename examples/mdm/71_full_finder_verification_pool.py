#!/usr/bin/env python3
"""Build an answer-blind synthetic safety set from the full FinDER graph.

The test intentionally measures a narrow property: whether a supervisor
preserves a conflict and keeps a protected marker out of its answer when it is
given two *comparable* structured facts.  It is not an end-to-end category
routing or natural-question accuracy benchmark.

Only the completed graph extraction, workspace metadata, and graph fact
properties are read.  FinDER answer text and answer scores are never loaded.
The Neo4j/DozerDB queries are read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs" / "evaluation" / "mdm_fedcat"
PARTIALS = BASE / "fedcat-full-all-survivorship-v1" / "index_partial"
DEFAULT_OUT = BASE / "log2026-full-finder-verification-v1" / "verification.json"
RECOVERY_URI = "bolt://localhost:7797"
DATABASES = {
    "deepseek": "mdmdeepseek",
    "gptoss": "mdmgptoss",
    "minimax25": "mdmminimax25",
    "minimax27": "mdmminimax27",
}
SOURCE_PROVIDER = "deepseek"
FACT_LABELS = {"MonetaryAmount", "CashFlow"}
INVALID_TEXT = {"", "n/a", "na", "none", "not reported", "unknown", "null"}


def stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clean_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text


def valid_amount(value: Any) -> str | None:
    text = clean_text(value).replace(",", "")
    if text.lower() in INVALID_TEXT:
        return None
    if not re.fullmatch(r"[-+]?\$?\d+(?:\.\d+)?%?", text):
        return None
    return text


def valid_period(value: Any) -> str | None:
    text = clean_text(value)
    if text.lower() in INVALID_TEXT or not re.search(r"\d", text):
        return None
    return text


def valid_metric(value: Any) -> str | None:
    text = clean_text(value)
    if len(text) < 3 or text.lower() in INVALID_TEXT:
        return None
    return text


def mutate(value: str) -> str:
    """Create a material numeric disagreement while retaining its unit marker."""
    match = re.search(r"[-+]?\$?\d+(?:\.\d+)?", value.replace(",", ""))
    if not match:
        raise ValueError(f"not a numeric amount: {value!r}")
    raw = match.group()
    prefix = "$" if "$" in raw else ""
    number = float(raw.replace("$", ""))
    changed = number * 1.1 if number else 1.0
    rendered = f"{changed:.6f}".rstrip("0").rstrip(".")
    replacement = prefix + rendered
    return value[:match.start()] + replacement + value[match.end():]


def workspace_records(partials: Path) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(partials.glob("*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        provider = str(row.get("provider_id") or "")
        case_id = str(row.get("case_id") or "")
        if provider in DATABASES and case_id and not row.get("error"):
            records[(provider, case_id)] = row
    return records


def collect_facts(records: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    """Return conservative structured facts from one completed provider graph."""
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv(ROOT / ".env")
    source_workspaces = {
        str(row["workspace_id"]): row
        for (provider, _), row in records.items()
        if provider == SOURCE_PROVIDER and row.get("workspace_id")
    }
    driver = GraphDatabase.driver(
        RECOVERY_URI,
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    query = """
    MATCH (n)
    WHERE n._workspace_id STARTS WITH $prefix
      AND n.amount IS NOT NULL
      AND n.period IS NOT NULL
    RETURN n._workspace_id AS workspace, elementId(n) AS node_id,
           labels(n) AS labels, n.name AS metric, n.amount AS amount,
           n.period AS period, n.currency AS currency
    """
    try:
        with driver.session(database=DATABASES[SOURCE_PROVIDER]) as session:
            rows = session.run(
                query,
                prefix="fedcat-scenario-duplicate-aware-survivorship-v1-fibo-finance-core-",
            ).data()
    finally:
        driver.close()

    facts: list[dict[str, Any]] = []
    for row in rows:
        metadata = source_workspaces.get(str(row["workspace"]))
        if metadata is None:
            continue
        labels = set(row["labels"] or [])
        label = next((item for item in sorted(FACT_LABELS) if item in labels), None)
        amount = valid_amount(row["amount"])
        period = valid_period(row["period"])
        metric = valid_metric(row["metric"])
        if not (label and amount and period and metric):
            continue
        case_id = str(metadata["case_id"])
        targets = sorted(
            provider
            for provider in DATABASES
            if provider != SOURCE_PROVIDER and (provider, case_id) in records
        )
        if not targets:
            continue
        facts.append({
            "case_id": case_id,
            "category": str(metadata["category"]),
            "source_provider": SOURCE_PROVIDER,
            "target_provider": targets[0],
            "source_workspace": str(row["workspace"]),
            "source_node_id": str(row["node_id"]),
            "labels": sorted(labels),
            "metric": metric,
            "period": period,
            "value": amount,
            "currency": clean_text(row["currency"]),
            "basis": f"structured_{label}",
        })
    return facts


def select_balanced(facts: list[dict[str, Any]], *, per_category: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select at most one fact per source case, balanced across categories."""
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        by_category[fact["category"]].append(fact)

    selected: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    for category in sorted(by_category):
        used_cases: set[str] = set()
        candidates = sorted(
            by_category[category],
            key=lambda fact: stable_key(
                "|".join((fact["case_id"], fact["metric"], fact["period"], fact["value"]))
            ),
        )
        chosen = 0
        for fact in candidates:
            if fact["case_id"] in used_cases:
                rejections.append({"case_id": fact["case_id"], "category": category, "reason": "duplicate_source_case"})
                continue
            if chosen >= per_category:
                rejections.append({"case_id": fact["case_id"], "category": category, "reason": "category_quota"})
                continue
            used_cases.add(fact["case_id"])
            chosen += 1
            original = {
                "metric": fact["metric"],
                "period": fact["period"],
                "value": fact["value"],
                "basis": fact["basis"],
                "currency": fact["currency"],
                "source": fact["source_provider"],
                "workspace": fact["source_workspace"],
                "node_id": fact["source_node_id"],
            }
            poisoned = dict(original)
            poisoned["value"] = mutate(original["value"])
            poisoned["source"] = fact["target_provider"]
            marker = f"SDCR_PROTECTED_{fact['case_id']}_{fact['source_node_id'][-8:]}"
            selected.append({
                "query_id": f"full-verification-{fact['case_id']}-{fact['source_node_id'][-8:]}",
                "case_id": fact["case_id"],
                "category": category,
                "selection_uses_answer_scores": False,
                "selection_uses_finder_answer_text": False,
                "original_fact": original,
                "poisoned_fact": poisoned,
                "target_provider": fact["target_provider"],
                "synthetic_marker": marker,
                "comparable": True,
                "conflict_detected": original["value"] != poisoned["value"],
                "expected_mode": "verification_coalition",
            })
    return selected, rejections


def build(*, per_category: int, partials: Path = PARTIALS) -> dict[str, Any]:
    records = workspace_records(partials)
    facts = collect_facts(records)
    selected, rejections = select_balanced(facts, per_category=per_category)
    counts = {category: sum(row["category"] == category for row in selected) for category in sorted({row["category"] for row in facts})}
    return {
        "contract": "log2026.full_finder_verification_set.v1",
        "source_dataset": "FinDER full 5,703-case extraction",
        "database_access": "read-only",
        "selection": (
            "MonetaryAmount or CashFlow node with nonempty numeric amount, period, and metric; "
            "one source case per category selected by a deterministic hash. FinDER answers and answer scores are never loaded."
        ),
        "selection_uses_answer_scores": False,
        "selection_uses_finder_answer_text": False,
        "source_provider": SOURCE_PROVIDER,
        "target_provider_rule": "lexicographically first independently extracted provider for the same source case",
        "candidate_structured_facts": len(facts),
        "accepted": len(selected),
        "accepted_by_category": counts,
        "rejected": len(rejections),
        "claim_scope": (
            "Synthetic fact-conflict and protected-marker test. It validates the verification and evidence-filtering contract, "
            "not natural mixed-query routing, factual extraction accuracy, or end-to-end multi-agent answer improvement."
        ),
        "cases": selected,
        "rejections": rejections,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-category", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.per_category < 1:
        raise SystemExit("--per-category must be positive")
    payload = build(per_category=args.per_category)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "candidate_structured_facts": payload["candidate_structured_facts"],
        "accepted": payload["accepted"],
        "accepted_by_category": payload["accepted_by_category"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
