#!/usr/bin/env python3
"""Do independently extracted views actually disagree, and can they be compared?

The reported safety result injects conflicts into 31 hand-picked facts. That
demonstrates a contract, not a phenomenon. This measures the phenomenon on
natural data at full corpus scale, with no injection and no model calls.

Four providers extracted the same 5,703 FinDER cases into four isolated
databases. For a cross-view verifier to fire at all, two providers must first
produce a *comparable key* for the same fact. Only then can their values agree
or disagree. So this reports two rates, in order:

  1. comparable-key rate   how often two independent extractions of one case
                           name the same fact at all
  2. disagreement rate     among comparable pairs, how often the values differ

Both matter for the claim that graph federation earns its keep on verification
rather than on answer quality. A high disagreement rate says verification has
real work to do. A low comparable-key rate says the architecture cannot see the
work, whatever the router does, which is a harder finding and must not be hidden.

Read-only, no LLM or embedding calls. Requires the provider databases online.
Outputs outputs/evaluation/mdm_fedcat/log2026-natural-conflict-v1/.
"""
from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/evaluation/mdm_fedcat/log2026-natural-conflict-v1"
URI = os.getenv("SEOCHO_BOLT_URI", "bolt://localhost:7687")
DATABASES = {
    "deepseek": "mdmdeepseek",
    "gptoss": "mdmgptoss",
    "minimax25": "mdmminimax25",
    "minimax27": "mdmminimax27",
}
FACT_LABELS = ("MonetaryAmount", "CashFlow")
INVALID = {"", "n/a", "na", "none", "not reported", "unknown", "null", "-"}

FACT_QUERY = f"""
MATCH (n)
WHERE any(l IN labels(n) WHERE l IN {list(FACT_LABELS)})
  AND n.id IS NOT NULL AND n.workspace_id IS NOT NULL
RETURN n.id AS slug,
       coalesce(n.amount, n.value, '') AS amount,
       coalesce(n.currency, '') AS unit,
       coalesce(n.period, '') AS period,
       n.workspace_id AS workspace,
       labels(n) AS labels
"""


def case_of(workspace: str) -> str:
    """fedcat-deepseek-76e5193f -> 76e5193f; the trailing segment is the case."""
    return workspace.rsplit("-", 1)[-1].strip().lower()


def norm_slug(slug: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(slug).strip().lower()).strip("_")


def norm_value(raw: str) -> tuple[str, float | None]:
    """Return (canonical string, numeric value if parseable).

    Formatting must not be mistaken for disagreement: "$66,669", "66669" and
    "66669.00" are the same claim. Scale words are kept in the canonical string
    but applied to the number, so "1.2 billion" and "1200000000" compare equal.
    """
    text = str(raw).strip().lower()
    if text in INVALID:
        return ("", None)
    canon = re.sub(r"[\s,]", "", text)
    scale = 1.0
    for word, mult in (("trillion", 1e12), ("billion", 1e9), ("million", 1e6),
                       ("thousand", 1e3), ("bn", 1e9), ("mm", 1e6), ("m", 1e6),
                       ("k", 1e3)):
        if canon.endswith(word):
            scale = mult
            canon = canon[: -len(word)]
            break
    body = re.sub(r"[^0-9.\-]", "", canon)
    if body in ("", "-", ".", "-."):
        return (canon, None)
    try:
        return (canon, float(body) * scale)
    except ValueError:
        return (canon, None)


def classify(a: tuple, b: tuple) -> str:
    """Name the disagreement so the rate is interpretable."""
    na, nb = a[1], b[1]
    if na is None or nb is None:
        return "unparseable_on_one_side"
    if na == 0 or nb == 0:
        return "zero_versus_nonzero"
    if (na < 0) != (nb < 0):
        return "sign_flip"
    ratio = max(abs(na), abs(nb)) / min(abs(na), abs(nb))
    for power, name in ((1e9, "scale_1e9"), (1e6, "scale_1e6"), (1e3, "scale_1e3")):
        if abs(ratio - power) / power < 0.01:
            return name
    if ratio < 1.05:
        return "rounding_within_5pct"
    return "different_value"


def values_agree(a: tuple[str, float | None], b: tuple[str, float | None]) -> bool:
    """Numeric comparison when both parse, exact string otherwise."""
    if a[1] is not None and b[1] is not None:
        big = max(abs(a[1]), abs(b[1]), 1.0)
        return abs(a[1] - b[1]) / big < 1e-6
    return a[0] == b[0] and a[0] != ""


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from neo4j import GraphDatabase

    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise SystemExit("NEO4J_PASSWORD missing")
    driver = GraphDatabase.driver(URI, auth=(os.getenv("NEO4J_USER", "neo4j"), password))

    # provider -> (case, slug) -> list of observed values
    facts: dict[str, dict[tuple[str, str], list[tuple[str, float | None, str]]]] = {}
    totals: dict[str, dict[str, int]] = {}
    try:
        for provider, database in DATABASES.items():
            table: dict[tuple[str, str], list[tuple[str, float | None, str]]] = {}
            seen = unusable = 0
            with driver.session(database=database) as session:
                for row in session.run(FACT_QUERY):
                    seen += 1
                    slug = norm_slug(row["slug"])
                    case = case_of(row["workspace"])
                    canon, number = norm_value(row["amount"])
                    if not slug or not case or canon == "":
                        unusable += 1
                        continue
                    table.setdefault((case, slug), []).append(
                        (canon, number, str(row["unit"]).strip().lower(),
                         str(row["amount"]).strip()))
            facts[provider] = table
            totals[provider] = {"fact_nodes_seen": seen,
                                "unusable_value_or_key": unusable,
                                "distinct_case_slug_keys": len(table)}
            print(f"{provider}: {seen} fact nodes, {len(table)} usable keys, "
                  f"{unusable} unusable")
    finally:
        driver.close()

    # 1. comparable-key rate ------------------------------------------------
    key_owners: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for provider, table in facts.items():
        for key in table:
            key_owners[key].add(provider)
    shared = {k: v for k, v in key_owners.items() if len(v) >= 2}
    coverage = collections.Counter(len(v) for v in key_owners.values())

    # 2. disagreement among comparable pairs --------------------------------
    pairs = agree = disagree = unit_mismatch = 0
    kinds: dict[str, int] = collections.Counter()
    examples: list[dict[str, Any]] = []
    per_pair: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"pairs": 0, "agree": 0, "disagree": 0})
    ordered = sorted(DATABASES)
    for key, owners in shared.items():
        present = sorted(owners)
        for i, left in enumerate(present):
            for right in present[i + 1:]:
                lv = facts[left][key][0]
                rv = facts[right][key][0]
                pairs += 1
                label = f"{left}|{right}"
                per_pair[label]["pairs"] += 1
                if values_agree((lv[0], lv[1]), (rv[0], rv[1])):
                    agree += 1
                    per_pair[label]["agree"] += 1
                else:
                    disagree += 1
                    per_pair[label]["disagree"] += 1
                    if lv[2] != rv[2]:
                        unit_mismatch += 1
                    kind = classify(lv, rv)
                    kinds[kind] += 1
                    if len(examples) < 60:
                        examples.append({
                            "case": key[0], "slug": key[1], "kind": kind,
                            left: {"raw": lv[3], "number": lv[1], "unit": lv[2]},
                            right: {"raw": rv[3], "number": rv[1], "unit": rv[2]},
                            "unit_differs": lv[2] != rv[2],
                        })

    # 3. does the shipped deterministic verifier surface these? -------------
    import sys
    sys.path.insert(0, str(ROOT))
    from seocho.query.sdcr import Evidence, verify_conflicts

    checked = surfaced = 0
    for ex in examples:
        providers = [k for k in ex if k in DATABASES]
        if len(providers) < 2:
            continue
        evidence = [Evidence(source_id=f"{p}:{ex['case']}", view_id=p,
                             slot=ex["slug"], value=ex[p]["raw"])
                    for p in providers]
        checked += 1
        if ex["slug"] in verify_conflicts(evidence)["conflicts"]:
            surfaced += 1

    total_keys = len(key_owners)
    summary = {
        "providers": ordered,
        "per_provider": totals,
        "distinct_keys_any_provider": total_keys,
        "keys_in_two_or_more_providers": len(shared),
        "comparable_key_rate": round(len(shared) / total_keys, 6) if total_keys else 0.0,
        "key_coverage_histogram": {f"{k}_providers": v for k, v in sorted(coverage.items())},
        "comparable_pairs": pairs,
        "agree": agree,
        "disagree": disagree,
        "natural_disagreement_rate": round(disagree / pairs, 6) if pairs else 0.0,
        "disagreements_with_differing_unit": unit_mismatch,
        "per_provider_pair": dict(per_pair),
        "disagreement_kinds": dict(kinds.most_common()),
        "deterministic_verifier": {
            "conflicts_checked": checked,
            "conflicts_surfaced": surfaced,
            "note": "applies seocho.query.sdcr.verify_conflicts to real disagreeing pairs",
        },
    }
    payload = {
        "contract": "log2026.natural_conflict_census.v1",
        "method": ("full-corpus census over four independent provider extractions; "
                   "no conflict injection, no LLM or embedding calls, read-only"),
        "comparison_key": "(source case, normalized fact slug)",
        "value_normalization": ("case-folded, separators removed, scale words applied; "
                               "numeric comparison when both sides parse, exact "
                               "string otherwise"),
        "claim_boundary": ("The comparable-key rate bounds what any cross-view "
                           "verifier can see, independently of routing. The "
                           "disagreement rate is measured only on comparable pairs."),
        "summary": summary,
        "disagreement_examples": examples,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "natural_conflict_census.json").write_text(json.dumps(payload, indent=2) + "\n")

    lines = [
        "# Natural Cross-View Conflict Census (zero cost)", "",
        f"Providers: {', '.join(ordered)}", "",
        "## 1. Can independent extractions even be compared?", "",
        f"- Distinct (case, fact) keys across all providers: {total_keys:,}",
        f"- Keys present in two or more providers: {len(shared):,}",
        f"- **Comparable-key rate: {summary['comparable_key_rate']:.3f}**", "",
        "| Providers naming the same fact | Keys |", "|---|---:|",
    ]
    for k, v in sorted(coverage.items()):
        lines.append(f"| {k} | {v:,} |")
    lines += [
        "", "## 2. Do comparable views disagree?", "",
        f"- Comparable pairs: {pairs:,}",
        f"- Agree: {agree:,}",
        f"- Disagree: {disagree:,}",
        f"- **Natural disagreement rate: {summary['natural_disagreement_rate']:.3f}**",
        f"- Of the disagreements, unit or currency also differs: {unit_mismatch:,}", "",
        "| Disagreement kind | Pairs |", "|---|---:|",
    ] + [f"| {k} | {v:,} |" for k, v in kinds.most_common()] + [
        "", "## 3. Does the shipped verifier surface them?", "",
        f"- Checked: {checked}  surfaced: {surfaced}", "",
        payload["claim_boundary"], "",
    ]
    (OUT / "natural_conflict_census.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
