#!/usr/bin/env python3
"""Collect every experiment result into one append-only ledger.

Results in this experiment live in about twenty JSON files under ``outputs/finbench/``,
each written by the script that produced it. That is right for reproducibility and useless
for reading: there is no single place that answers "what has been measured, on which data,
and when". Worse, several conclusions in this project were stated at a generality the data
did not support, and the reason was always the same — the number and the conditions it was
measured under lived in different places.

This walks the output tree, extracts the headline figure and the conditions from each report
by its ``schema_version``, and appends to ``outputs/finbench/results.jsonl``. Append-only
because a result is a historical fact: when a later run supersedes an earlier one, both stay,
and the ledger shows the change rather than hiding it. Entries are keyed by
``(schema_version, source_file, content_hash)`` so re-running is idempotent — the same report
does not accumulate duplicate rows, but an *edited* report appends a new one.

Every entry carries the data it was measured on wherever the report names it, because
"aggregate times out" is true of one graph and false of another, and that distinction is the
single most repeated mistake in this project's failure log.

Usage:
    python scripts/finbench/results_log.py --collect
    python scripts/finbench/results_log.py --summary
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

LEDGER = Path("outputs/finbench/results.jsonl")


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract(report: Dict[str, Any], path: Path) -> Optional[Dict[str, Any]]:
    """Pull the headline figure and the conditions out of one report.

    Keyed on ``schema_version`` rather than filename so a renamed output still parses, and
    so an unrecognised schema is reported as such instead of being silently skipped.
    """
    sv = str(report.get("schema_version") or "")
    common = {"schema_version": sv, "source": str(path)}

    if sv.startswith("seocho.finbench.workload-gate"):
        m = report.get("verdict_matrix") or {}
        total = sum(m.values()) if m else 0
        return {**common, "experiment": "workload_gate",
                "dataset": report.get("cost_model"),
                "headline": {
                    "cases": total,
                    "false_clear": m.get("false_clear"),
                    "false_flag": m.get("false_flag"),
                    "agreement": (round((m.get("true_clear", 0) + m.get("true_flag", 0))
                                        / total, 4) if total else None)},
                "conditions": {"budget_rows": report.get("budget_rows"),
                               "engine": report.get("engine"),
                               "db_hits_per_l2": report.get("db_hits_per_l2"),
                               "cases_file": report.get("cases")}}

    if sv.startswith("seocho.finbench.driver-overhead"):
        probes = [p for p in report.get("probes", []) if p.get("overhead_share") is not None]
        worst = max(probes, key=lambda p: p["overhead_share"]) if probes else None
        return {**common, "experiment": "driver_overhead",
                "dataset": report.get("database"),
                "headline": {"worst_probe": worst and worst["probe"],
                             "worst_client_share": worst and worst["overhead_share"],
                             "worst_client_ms": worst and worst["client_overhead_ms"]},
                "conditions": {"anchor": report.get("anchor"),
                               "repeats": report.get("repeats")}}

    if sv.startswith("seocho.finbench.curated-parameters"):
        bands = report.get("bands", [])
        ratios = [(a["db_hits"] / a["l2"]) for b in bands for a in b.get("anchors", [])
                  if a.get("db_hits") and a.get("l2")]
        return {**common, "experiment": "cost_model_calibration",
                "dataset": report.get("source"),
                "headline": {"bands": len(bands),
                             "anchors_measured": len(ratios),
                             "ratio_min": round(min(ratios), 3) if ratios else None,
                             "ratio_max": round(max(ratios), 3) if ratios else None},
                "conditions": {"method": report.get("method"),
                               "validated_against": report.get("validated_against")}}

    if sv.startswith("seocho.finbench.hub-degree"):
        rows = report.get("results", [])
        return {**common, "experiment": "hub_degree_probe",
                "dataset": report.get("database"),
                "headline": {"arms": sorted({r["arm"] for r in rows}),
                             "timeouts": sum(1 for r in rows if r.get("timed_out"))},
                "conditions": {"hub_skew": report.get("hub_skew"),
                               "truncation_limit": report.get("truncation_limit")}}

    if sv.startswith("seocho.finbench.ablation-ontology"):
        arms = report.get("arms", {})
        return {**common, "experiment": "ontology_comparison",
                "dataset": report.get("database"),
                "headline": {arm: {"accuracy": _num((a or {}).get("accuracy")),
                                   "db_hits": ((a or {}).get("stages") or {})
                                              .get("s4_db_hits_total")}
                             for arm, a in arms.items() if isinstance(a, dict)},
                "conditions": {"model": report.get("model")}}

    if sv.startswith("seocho.finbench.cliff"):
        steps = report.get("steps", [])
        return {**common, "experiment": "memory_sweep",
                "dataset": report.get("database"),
                "headline": {"profiles": len(steps),
                             "failed_to_start": sum(1 for s in steps if s.get("error"))},
                "conditions": {"cypher": report.get("cypher")}}

    if sv.startswith("seocho.finbench.neo4rs-probe"):
        probes = {p["probe"]: p for p in report.get("probes", [])}
        big = probes.get("rows_50k", {})
        abort = probes.get("early_abort_50_of_unbounded", {})
        return {**common, "experiment": "native_driver_probe",
                "dataset": report.get("database"),
                "headline": {"driver": report.get("driver"),
                             "decode_50k_ms": big.get("decode_ms"),
                             "per_row_us": big.get("per_row_us"),
                             "early_abort_ms": abort.get("abort_ms"),
                             "early_abort_saving": abort.get("saving_ratio")},
                "conditions": {"repeats": report.get("repeats")}}

    if sv.startswith("seocho.finbench.mara-breakdown") or "models" in report:
        models = report.get("models") or []
        return {**common, "experiment": "model_breakdown",
                "dataset": report.get("database"),
                "headline": {m.get("model"): {
                    "accuracy": _num(m.get("accuracy")),
                    "sargable": (m.get("stages") or {}).get("s4_sargable_rate"),
                    "db_hits": (m.get("stages") or {}).get("s4_db_hits_total")}
                    for m in models if isinstance(m, dict)},
                "conditions": {"cases": report.get("cases_file") or report.get("cases")}}

    return {**common, "experiment": "unrecognised",
            "note": "no extractor for this schema_version; add one rather than dropping it"}


def collect(root: Path, ledger: Path) -> Dict[str, int]:
    seen = set()
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            seen.add((e.get("schema_version"), e.get("source"),
                      e.get("content_hash"), e.get("extract_hash")))

    added, skipped, unparsed = 0, 0, 0
    new_lines: List[str] = []
    for path in sorted(root.rglob("*.json")):
        if path.name == "results.jsonl" or path.name == "manifest.json":
            continue
        try:
            raw = path.read_text()
            report = json.loads(raw)
        except Exception:
            unparsed += 1
            continue
        if not isinstance(report, dict) or "schema_version" not in report:
            continue
        entry = _extract(report, path)
        if entry is None:
            continue
        # Hash the *extraction*, not only the source. Keying on the source alone means an
        # improved extractor silently produces nothing — the first version of this did
        # exactly that, reporting 40 already-logged and 0 added after the extractors
        # changed. "We now read this report differently" is itself a fact worth a row.
        entry["content_hash"] = hashlib.sha256(raw.encode()).hexdigest()[:16]
        entry["extract_hash"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]
        key = (entry["schema_version"], entry["source"], entry["content_hash"],
               entry["extract_hash"])
        if key in seen:
            skipped += 1
            continue
        new_lines.append(json.dumps(entry, ensure_ascii=False))
        added += 1

    if new_lines:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a") as fh:
            fh.write("\n".join(new_lines) + "\n")
    return {"added": added, "already_logged": skipped, "unparsed": unparsed}


def summarise(ledger: Path) -> str:
    if not ledger.exists():
        return "no ledger yet — run with --collect\n"
    entries = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
    lines = ["# Experiment results ledger", "",
             f"{len(entries)} entries in `{ledger}`. Append-only: a superseded result stays "
             "so the change is visible rather than hidden.", "",
             "| experiment | dataset | headline | source |",
             "|---|---|---|---|"]
    for e in entries:
        head = e.get("headline") or e.get("note") or ""
        head = json.dumps(head, ensure_ascii=False) if isinstance(head, dict) else str(head)
        if len(head) > 110:
            head = head[:107] + "…"
        lines.append(f"| {e.get('experiment')} | `{e.get('dataset') or '—'}` | {head} | "
                     f"`{Path(e.get('source', '')).name}` |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("outputs/finbench"))
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    if args.collect:
        stats = collect(args.root, args.ledger)
        print(f"[log] {stats}")
    if args.summary or not args.collect:
        md = summarise(args.ledger)
        args.ledger.with_suffix(".md").write_text(md)
        print(md)


if __name__ == "__main__":
    main()
