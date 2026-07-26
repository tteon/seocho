#!/usr/bin/env python3
"""Zero-cost diagnostic: how much does the issuer defect change the pool?

The frozen candidate pool keyed issuers on "last uppercase 2-5 letter token not
in a small stop list", which selects trailing accounting acronyms and can merge
two companies under one label (a United Airlines question with a Cintas question
under EPS). This script quantifies the damage and produces a corrected pool,
without replacing the reported 13-case chain -- re-deriving that chain would
require the paid five-role persona gate.

The corrected extractor reuses the identifier-first policy already reported in
the paper rather than inventing a new ticker list: a token qualifies only if it
appears as an accepted ``ticker:*`` in the frozen identity registry (must-link
requires >=2 providers or >=2 categories; conflicting tickers are quarantined).
A question naming more than one accepted ticker is ambiguous and is dropped,
which is the same cannot-link discipline applied at the question level.

Also emits a core-disjoint version of the 120-query expansion pool, since the
frozen manifest overlaps the evaluated core on 10 of 13 candidate ids.

No LLM, embedding, or database calls. Outputs
``outputs/evaluation/mdm_fedcat/log2026-validated-issuer-pool-v1/``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from itertools import combinations
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "outputs/evaluation/mdm_fedcat"
CROSS = BASE / "log2026-full-finder-cross-view-v1"
OUT = BASE / "log2026-validated-issuer-pool-v1"

# Verbatim from 37_full_finder_cross_view_pool.py so the only variable that moves
# is issuer identification.
LEGACY_EXCLUDED = {"ASC", "GAAP", "SEC", "EBITDA", "US", "FY", "CEO", "CFO", "ERM", "CISO", "IT"}
AXES = {
    "liquidity_capital_allocation": r"liquid|cash|capital alloc|repurchas|buyback|dividend|debt|borrow|working capital|contract asset|contract liabil",
    "enterprise_risk": r"risk|cyber|security|litig|legal|settle|judg|regulat|contingen|qui tam|disruption",
    "profitability_growth": r"profit|growth|margin|earnings|eps|revenue|cost of sales|performance",
    "governance_audit": r"govern|board|audit|oversight|control|compliance|ciso|erm",
}
LIMIT = 240


def load_cases() -> list[dict[str, Any]]:
    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_cases_full(seed=42)


def accepted_tickers() -> set[str]:
    registry = json.loads(
        (BASE / "log2026-clean-entity-network-v1/analysis.json").read_text()
    )["identity_registry"]["accepted"]
    return {key.split(":", 1)[1].upper() for key in registry if key.startswith("ticker:")}


def legacy_issuer(question: str) -> str:
    tokens = [t for t in re.findall(r"\b[A-Z]{2,5}\b", question) if t not in LEGACY_EXCLUDED]
    return tokens[-1] if tokens else ""


def validated_issuer(question: str, tickers: set[str]) -> str:
    """Exactly one accepted ticker, or nothing. Ambiguity is a cannot-link."""
    found = {t for t in re.findall(r"\b[A-Z]{2,5}\b", question) if t in tickers}
    return next(iter(found)) if len(found) == 1 else ""


def decision_axes(question: str) -> set[str]:
    return {axis for axis, pattern in AXES.items() if re.search(pattern, question, re.I)}


def stable_fraction(value: str) -> float:
    return int(hashlib.sha256(value.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def build_pool(cases: list[dict[str, Any]], resolver) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        issuer = resolver(str(case["query"]))
        axes = decision_axes(str(case["query"]))
        if issuer and axes:
            grouped.setdefault(issuer, []).append({**case, "decision_axes": sorted(axes)})
    candidates = []
    for issuer, rows in sorted(grouped.items()):
        best: dict[tuple[str, str], dict[str, Any]] = {}
        for left, right in combinations(sorted(rows, key=lambda r: str(r["case_id"])), 2):
            if left["category"] == right["category"]:
                continue
            shared = sorted(set(left["decision_axes"]) & set(right["decision_axes"]))
            if not shared:
                continue
            key = tuple(sorted((str(left["category"]), str(right["category"]))))
            score = len(shared) * 10 + len(set(left["decision_axes"]) | set(right["decision_axes"]))
            cid = f"{issuer.lower()}-{left['case_id']}-{right['case_id']}"
            row = {
                "candidate_id": cid,
                "issuer": issuer,
                "required_categories": [left["category"], right["category"]],
                "component_case_ids": [left["case_id"], right["case_id"]],
                "shared_decision_axes": shared,
                "score": score,
                "split": "development" if stable_fraction(issuer) < 0.25 else "held_out",
            }
            if key not in best or (score, cid) > (best[key]["score"], best[key]["candidate_id"]):
                best[key] = row
        candidates.extend(best.values())
    return sorted(
        candidates,
        key=lambda r: (-r["score"], stable_fraction(r["candidate_id"]), r["candidate_id"]),
    )[:LIMIT]


def main() -> int:
    cases = load_cases()
    tickers = accepted_tickers()
    frozen = json.loads((CROSS / "candidates.json").read_text())["candidates"]
    validation = json.loads((CROSS / "revised_blind_validation.json").read_text())
    core = [cid for cid, v in validation["decisions"].items() if v["decision"] == "accept"]

    corrected = build_pool(cases, lambda q: validated_issuer(q, tickers))

    frozen_ids = {r["candidate_id"] for r in frozen}
    corrected_ids = {r["candidate_id"] for r in corrected}
    frozen_by_id = {r["candidate_id"]: r for r in frozen}

    # Which frozen candidates pair two different validated issuers?
    merged: list[dict[str, Any]] = []
    for row in frozen:
        qs = [str(c["query"]) for c in cases if c["case_id"] in row["component_case_ids"]]
        resolved = sorted({validated_issuer(q, tickers) for q in qs} - {""})
        if len(resolved) > 1:
            merged.append({
                "candidate_id": row["candidate_id"],
                "assigned_issuer": row["issuer"],
                "distinct_validated_issuers": resolved,
                "in_evaluated_core": row["candidate_id"] in core,
            })

    unvalidated = sorted({r["issuer"] for r in frozen if r["issuer"].upper() not in tickers})

    # Core-disjoint expansion pool.
    manifest = json.loads((CROSS / "expanded_query_manifest_v1.json").read_text())
    core_issuers = {cid.split("-")[0].upper() for cid in core}
    kept = [q for q in manifest["queries"]
            if q["candidate_id"] not in set(core)
            and q["issuer"].upper() not in core_issuers]
    disjoint = {
        "contract": "log2026.expanded_query_manifest.v2",
        "status": "pending_construct_validation",
        "derived_from": "expanded_query_manifest_v1.json",
        "change": (
            "removed every query sharing a candidate id or issuer with the evaluated "
            "13-query core, so the pool is disjoint from reported results"
        ),
        "removed_candidate_ids": sorted(set(core) & {q["candidate_id"] for q in manifest["queries"]}),
        "removed_for_issuer_overlap": sorted(
            q["candidate_id"] for q in manifest["queries"]
            if q["candidate_id"] not in set(core) and q["issuer"].upper() in core_issuers
        ),
        "count": len(kept),
        "unique_issuers": len({q["issuer"] for q in kept}),
        "issuer_labels_use_validated_tickers": False,
        "queries": kept,
    }

    payload = {
        "contract": "log2026.validated_issuer_pool.v1",
        "method": "deterministic replay; no LLM, embedding, or database calls",
        "ticker_source": "log2026-clean-entity-network-v1 identity_registry.accepted",
        "accepted_tickers": len(tickers),
        "claim_boundary": (
            "Diagnostic only. The reported 13-case chain is NOT replaced: re-deriving "
            "it needs the paid five-role persona gate. This quantifies the defect and "
            "provides the corrected pool for that future run."
        ),
        "frozen_pool": {
            "candidates": len(frozen),
            "distinct_issuers": len({r["issuer"] for r in frozen}),
            "issuers_not_validated_tickers": unvalidated,
            "candidates_merging_two_issuers": merged,
        },
        "corrected_pool": {
            "candidates": len(corrected),
            "distinct_issuers": len({r["issuer"] for r in corrected}),
            "development": sum(r["split"] == "development" for r in corrected),
            "held_out": sum(r["split"] == "held_out" for r in corrected),
        },
        "overlap": {
            "shared_candidate_ids": len(frozen_ids & corrected_ids),
            "frozen_only": len(frozen_ids - corrected_ids),
            "corrected_only": len(corrected_ids - frozen_ids),
        },
        "evaluated_core_status": {
            "core_size": len(core),
            "survives_corrected_extractor": sorted(cid for cid in core if cid in corrected_ids),
            "dropped_by_corrected_extractor": sorted(cid for cid in core if cid not in corrected_ids),
            "core_issuers_all_validated_tickers": sorted(
                i for i in {frozen_by_id[c]["issuer"] for c in core if c in frozen_by_id}
                if i.upper() not in tickers
            ) or "all validated",
        },
        "expansion_pool_v2": {k: v for k, v in disjoint.items() if k != "queries"},
        "corrected_candidates": corrected,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "validated_issuer_pool.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUT / "expanded_query_manifest_v2.json").write_text(json.dumps(disjoint, indent=2) + "\n")

    lines = [
        "# Validated-Issuer Pool Diagnostic (zero cost)",
        "",
        f"Accepted tickers in the frozen identity registry: {len(tickers)}",
        "",
        "## Damage in the frozen 240-candidate pool",
        "",
        f"- Issuer labels that are not validated tickers: {len(unvalidated)} "
        f"({', '.join(unvalidated) if unvalidated else 'none'})",
        f"- Candidates pairing two DIFFERENT validated issuers: {len(merged)}",
    ]
    for row in merged:
        lines.append(
            f"  - `{row['candidate_id']}` assigned `{row['assigned_issuer']}` but pairs "
            f"{' + '.join(row['distinct_validated_issuers'])}"
            f"{'  **(in evaluated core)**' if row['in_evaluated_core'] else ''}"
        )
    lines += [
        "",
        "## Corrected pool",
        "",
        f"- Candidates: {payload['corrected_pool']['candidates']} "
        f"(dev {payload['corrected_pool']['development']}, "
        f"held out {payload['corrected_pool']['held_out']})",
        f"- Distinct issuers: {payload['corrected_pool']['distinct_issuers']}",
        f"- Shared with frozen pool: {payload['overlap']['shared_candidate_ids']}; "
        f"frozen-only {payload['overlap']['frozen_only']}; "
        f"corrected-only {payload['overlap']['corrected_only']}",
        "",
        "## Evaluated 13-case core",
        "",
        f"- Survives the corrected extractor: "
        f"{len(payload['evaluated_core_status']['survives_corrected_extractor'])}/{len(core)}",
        f"- Core issuers that are not validated tickers: "
        f"{payload['evaluated_core_status']['core_issuers_all_validated_tickers']}",
        "",
        "## Expansion pool v2 (core-disjoint)",
        "",
        f"- Queries: {disjoint['count']} (was {manifest['count']}), "
        f"unique issuers {disjoint['unique_issuers']}",
        f"- Removed for candidate-id overlap: {len(disjoint['removed_candidate_ids'])}",
        f"- Removed for issuer overlap: {len(disjoint['removed_for_issuer_overlap'])}",
        "",
        payload["claim_boundary"],
        "",
    ]
    (OUT / "validated_issuer_pool.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
