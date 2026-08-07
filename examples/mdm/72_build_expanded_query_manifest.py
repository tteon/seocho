#!/usr/bin/env python3
"""Build a frozen, issuer-clustered expansion pool from FinDER candidates.

This does not promote candidates to gold. Candidates remain pending until the
LLM expert-proxy gate (or human review) accepts their construct validity.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-full-finder-cross-view-v1"
OUT = BASE / "expanded_query_manifest_v1.json"


def key(row: dict[str, Any]) -> str:
    return hashlib.sha256(row["candidate_id"].encode()).hexdigest()


def main() -> int:
    data = json.loads((BASE / "candidates.json").read_text())
    candidates = [r for r in data["candidates"] if r.get("split") == "held_out"]
    # Keep issuer clusters intact: one candidate per issuer in the expansion.
    selected: list[dict[str, Any]] = []
    seen_issuers: set[str] = set()
    for row in sorted(candidates, key=key):
        issuer = str(row["issuer"])
        if issuer in seen_issuers:
            continue
        seen_issuers.add(issuer)
        selected.append(row)
        if len(selected) >= 120:
            break
    category_pairs = Counter("|".join(sorted(row["required_categories"])) for row in selected)
    revised = json.loads((BASE / "revised_integrative_candidates.json").read_text())
    core_ids = [row["candidate_id"] for row in revised["rows"]]
    payload = {
        "contract": "log2026.expanded_query_manifest.v1",
        "status": "pending_construct_validation",
        "selection": "deterministic SHA-256 order, held-out split, at most one query per issuer",
        "count": len(selected),
        "unique_issuers": len({row["issuer"] for row in selected}),
        "category_pair_counts": dict(sorted(category_pairs.items())),
        "core_query_ids": core_ids,
        "queries": selected,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: payload[k] for k in ("count", "unique_issuers", "category_pair_counts")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
