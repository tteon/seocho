#!/usr/bin/env python3
"""Audit what a reviewer would receive if they asked for the data.

A reviewer asking "show me the run behind table 3" should get a directory that
answers, without anyone present to explain it, four questions:

    what was fixed      the configuration that determines the result, hashed
    what happened       every step, with what went in and what came out
    what was said       every model call, request written before it was issued
    what was asked      every database statement, with the server's own timing

This checks each run directory for all four and reports what is missing. It also
checks the things that are easy to have and easy to have wrong: a stage that
recorded an input and no output, a payload truncated without saying so, an
OpenTelemetry span file that is not valid, two runs sharing a fingerprint but
disagreeing on their numbers.

    python3 experiments/audit_observability.py            summary per run
    python3 experiments/audit_observability.py --claims   per published claim
    python3 experiments/audit_observability.py --strict   non-zero if incomplete

The --claims view is the one to hand a reviewer: it lists each result the paper
cites and the exact path to the evidence behind it.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "outputs/minimal"

REQUIRED_FILES = ("decisive.json", "config.resolved.json", "trace.jsonl",
                  "spans.jsonl", "run.log", "result.json")

# Span attributes that make a record useful to someone who was not here. The
# gen_ai.* names are OpenTelemetry's own semantic conventions; fed.* and the
# fingerprint are ours and are declared in semconv.py.
EXPECTED_SPAN_KEYS = ("run.fingerprint",)
LLM_SPAN_KEYS = ("gen_ai.request.model", "gen_ai.response.model")
DB_SPAN_KEYS = ("db.query.text", "db.system")


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Records and the count of lines that would not parse."""
    if not path.is_file():
        return [], 0
    records, broken = [], 0
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            broken += 1
    return records, broken


def truncation_count(value: Any) -> int:
    """How many payloads were cut. Cut payloads are marked, never silent."""
    if isinstance(value, dict):
        if value.get("_truncated"):
            return 1 + sum(truncation_count(v) for v in value.values())
        return sum(truncation_count(v) for v in value.values())
    if isinstance(value, list):
        return sum(truncation_count(v) for v in value)
    return 0


def audit_run(directory: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"run": directory.name,
                           "path": str(directory.relative_to(ROOT))}
    row["missing_files"] = [f for f in REQUIRED_FILES
                            if not (directory / f).is_file()]

    trace, trace_broken = read_jsonl(directory / "trace.jsonl")
    spans, span_broken = read_jsonl(directory / "spans.jsonl")
    driver, driver_broken = read_jsonl(directory / "driver.jsonl")

    stages = [r for r in trace if r.get("stage")
              and not str(r["stage"]).startswith("llm.")]
    # A stage that recorded what went in and nothing that came out is a step
    # nobody can check. Errors legitimately have no output and are excluded.
    silent = [r for r in stages
              if r.get("status") == "ok" and not r.get("output")]
    llm_requests = [r for r in trace if str(r.get("stage", "")).endswith(".request")
                    or r.get("stage") == "llm.request"]
    llm_responses = [r for r in trace if str(r.get("stage", "")).endswith(".response")
                     or r.get("stage") == "llm.response"]

    fingerprints = {r.get("fingerprint") for r in trace if r.get("fingerprint")}
    summary: dict[str, Any] = {}
    if (directory / "result.json").is_file():
        try:
            summary = json.loads((directory / "result.json").read_text())
        except json.JSONDecodeError:
            summary = {}

    span_attrs: Counter = Counter()
    for span in spans:
        span_attrs.update((span.get("attributes") or {}).keys())

    row.update({
        "stages": len(stages),
        "stages_with_no_output": len(silent),
        "stages_failed": sum(1 for r in stages if r.get("status") == "error"),
        "trace_unparseable_lines": trace_broken,
        "spans": len(spans),
        "spans_unparseable_lines": span_broken,
        "spans_with_fingerprint": sum(
            1 for s in spans
            if "run.fingerprint" in (s.get("attributes") or {})),
        "llm_requests": len(llm_requests),
        "llm_responses": len(llm_responses),
        "llm_request_before_response": len(llm_requests) >= len(llm_responses),
        "llm_spans_typed": sum(1 for k in LLM_SPAN_KEYS if span_attrs.get(k)),
        "db_statements": sum(1 for r in driver if r.get("kind") == "query"),
        "db_spans_typed": sum(1 for k in DB_SPAN_KEYS if span_attrs.get(k)),
        "db_server_timed": sum(1 for r in driver if r.get("kind") == "result"),
        "driver_unparseable_lines": driver_broken,
        "truncated_payloads": sum(truncation_count(r) for r in trace),
        "fingerprint": summary.get("fingerprint") or (
            sorted(fingerprints)[0] if fingerprints else ""),
        "fingerprints_in_trace": len(fingerprints),
        "seconds": summary.get("seconds", 0),
    })

    problems = []
    if row["missing_files"]:
        problems.append(f"missing {', '.join(row['missing_files'])}")
    if row["stages_with_no_output"]:
        problems.append(f"{row['stages_with_no_output']} stages recorded no output")
    if trace_broken or span_broken or driver_broken:
        problems.append("unparseable lines present")
    if row["fingerprints_in_trace"] > 1:
        problems.append("more than one fingerprint inside one run")
    if llm_responses and not row["llm_request_before_response"]:
        problems.append("a response without its request")
    if row["db_statements"] and not row["db_spans_typed"]:
        problems.append("database statements not reflected on spans")
    row["problems"] = problems
    row["complete"] = not problems and row["stages"] > 0
    return row


def fingerprint_conflicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs sharing a fingerprint must share their numbers.

    Two runs with the same declared-decisive configuration that report different
    results mean something outside that configuration moved, which is a defect
    to chase rather than a finding to publish.
    """
    by_fingerprint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["fingerprint"]:
            by_fingerprint[row["fingerprint"]].append(row)
    conflicts = []
    for fingerprint, group in by_fingerprint.items():
        if len(group) < 2:
            continue
        signatures, versions, dirty = set(), set(), False
        for row in group:
            path = ROOT / row["path"] / "result.json"
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            comparable = {k: v for k, v in payload.items()
                          if k not in ("seconds", "stages", "artifact", "driver",
                                       "code_version")}
            signatures.add(json.dumps(comparable, sort_keys=True, default=str))
            version = payload.get("code_version") or {}
            versions.add(version.get("commit", ""))
            dirty = dirty or bool(version.get("uncommitted_changes"))
        if len(signatures) > 1:
            # Two runs sharing a fingerprint and disagreeing is only a defect if
            # the code was also the same. The fingerprint covers declared
            # configuration and never covered the code, which is the gap the
            # recorded commit exists to expose rather than to close.
            if len(versions - {""}) > 1 or dirty:
                severity = "explained by a code change"
            elif "" in versions:
                severity = "unknown — these runs predate code-version recording"
            else:
                severity = "UNEXPLAINED — same config, same code, different result"
            conflicts.append({"fingerprint": fingerprint,
                              "runs": [r["run"] for r in group],
                              "distinct_results": len(signatures),
                              "code_versions": sorted(versions),
                              "severity": severity})
    return conflicts


def claim_view() -> list[dict[str, Any]]:
    """One row per published contract, with the path a reviewer would be sent."""
    index = ROOT / "experiments/results_index.json"
    if not index.is_file():
        return []
    payload = json.loads(index.read_text())
    newest: dict[str, dict[str, Any]] = {}
    for entry in payload.get("results", []):
        current = newest.get(entry["contract"])
        if current is None or entry["modified"] > current["modified"]:
            newest[entry["contract"]] = entry
    rows = []
    for contract, entry in sorted(newest.items()):
        run_dir = ROOT / entry["run_dir"]
        audited = audit_run(run_dir) if (run_dir / "trace.jsonl").is_file() else {}
        rows.append({
            "contract": contract,
            "artifact": entry["path"],
            "run_dir": entry["run_dir"],
            "traced": bool(audited),
            "stages": audited.get("stages", 0),
            "spans": audited.get("spans", 0),
            "llm_calls": audited.get("llm_responses", 0),
            "db_statements": audited.get("db_statements", 0),
            "complete": audited.get("complete", False),
            "problems": audited.get("problems", ["no trace directory"]),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--claims", action="store_true",
                    help="per published claim, the evidence a reviewer gets")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    rows = [audit_run(d) for d in sorted(RUNS.glob("*/"))
            if (d / "result.json").is_file() or (d / "trace.jsonl").is_file()]
    conflicts = fingerprint_conflicts(rows)

    if args.claims:
        claims = claim_view()
        print(f"{'contract':40s} {'trace':6s} {'stages':>6s} {'spans':>6s} "
              f"{'llm':>5s} {'db':>6s}  evidence")
        for row in claims:
            print(f"{row['contract'][:40]:40s} "
                  f"{'yes' if row['traced'] else 'NO':6s} {row['stages']:6d} "
                  f"{row['spans']:6d} {row['llm_calls']:5d} "
                  f"{row['db_statements']:6d}  {row['run_dir']}")
        traced = sum(1 for r in claims if r["traced"])
        print(f"\n{traced} of {len(claims)} published contracts have a trace "
              f"directory behind them")
        untraced = [r["contract"] for r in claims if not r["traced"]]
        if untraced:
            print("\nno trace, so a reviewer would receive numbers with no record "
                  "of how they were produced:")
            for contract in untraced[:20]:
                print(f"  {contract}")
        if args.json:
            args.json.write_text(json.dumps({
                "contract": "seocho.observability_claims.v1",
                "question": "For each published result, what evidence exists?",
                "claim_boundary": ("Checks that a record exists and is "
                                   "well-formed. It does not check that the "
                                   "numbers in it are right."),
                "claims": claims}, indent=2) + "\n")
            print(f"\nwrote {args.json}")
        return 1 if (args.strict and untraced) else 0

    print(f"{'run':38s} {'stg':>4s} {'span':>5s} {'llm':>4s} {'db':>5s} "
          f"{'cut':>4s}  status")
    for row in rows:
        status = "complete" if row["complete"] else "; ".join(row["problems"])[:52]
        print(f"{row['run'][:38]:38s} {row['stages']:4d} {row['spans']:5d} "
              f"{row['llm_responses']:4d} {row['db_statements']:5d} "
              f"{row['truncated_payloads']:4d}  {status}")

    complete = sum(1 for r in rows if r["complete"])
    print(f"\n{complete} of {len(rows)} runs complete")
    totals = {
        "stages": sum(r["stages"] for r in rows),
        "spans": sum(r["spans"] for r in rows),
        "model calls": sum(r["llm_responses"] for r in rows),
        "database statements": sum(r["db_statements"] for r in rows),
        "truncated payloads": sum(r["truncated_payloads"] for r in rows),
        "stages with no output": sum(r["stages_with_no_output"] for r in rows),
    }
    print(f"totals: {totals}")
    if conflicts:
        unexplained = [c for c in conflicts if c["severity"].startswith("UNEX")]
        print(f"\nsame fingerprint, different results: {len(conflicts)} groups, "
              f"{len(unexplained)} unexplained")
        for entry in conflicts:
            print(f"  {entry['fingerprint']}  {len(entry['runs'])} runs  "
                  f"{entry['severity']}")
    else:
        print("no fingerprint carries two different results")

    if args.json:
        args.json.write_text(json.dumps({
            "contract": "seocho.observability_audit.v1",
            "question": ("Does every run leave a record a reviewer could read "
                         "without us present?"),
            "claim_boundary": ("Checks presence and well-formedness of the "
                               "record, not the correctness of what it "
                               "records."),
            "runs_complete": complete, "runs_total": len(rows),
            "totals": totals, "fingerprint_conflicts": conflicts,
            "runs": rows}, indent=2) + "\n")
        print(f"wrote {args.json}")

    if args.strict:
        return 1 if complete < len(rows) else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
