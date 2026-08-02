#!/usr/bin/env python3
"""Index every experiment result in the repository, so findings stop scattering.

Results currently land in three places — per-run directories under
outputs/minimal, older analysis artifacts under outputs/evaluation, and loose
JSON — and the only thing tying them together is that each carries a `contract`
field. That field is enough to build a catalogue from, and this script does.

    python3 experiments/registry.py            print the catalogue
    python3 experiments/registry.py --write    refresh RESULTS.md and the index
    python3 experiments/registry.py --lint     exit non-zero on convention breaks

What a result artifact must carry
---------------------------------
    contract          stable id with a version, e.g. log2026.arm_results.v1.
                      Bumping the version is how a rerun that changes meaning
                      announces itself; reusing it silently is how two numbers
                      end up with one name.
    question          the question the artifact answers, in one sentence
    claim_boundary    what the number does NOT support. Required, because every
                      finding here has a limit and the limit is the first thing
                      lost when a number is quoted in a paper.

Optional but honoured: `supersedes` (points at a contract this replaces, so a
withdrawn measurement stays visible instead of vanishing), `method`,
`held_fixed`, `moving`.

--lint enforces the three required fields and reports duplicate contracts, which
is how you find out that a script was run twice with different inputs and the
second run quietly overwrote the first's meaning.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
SEARCH = ("outputs/minimal", "outputs/evaluation")
INDEX = ROOT / "experiments/results_index.json"
CATALOGUE = ROOT / "experiments/RESULTS.md"
REQUIRED = ("contract", "question", "claim_boundary")

# Numbers worth surfacing in a one-line summary, in the order they read best.
HEADLINE = (
    "verdict", "by_arm", "suite_seconds", "annotation_coverage",
    "new_subclass_within_scope", "alias_more_common_than_label",
    "scored", "attempted", "counts",
)


def artifacts() -> Iterator[Path]:
    seen = set()
    for relative in SEARCH:
        base = ROOT / relative
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.json")):
            if path.name in ("config.resolved.json", "decisive.json",
                             "result.json", "results_index.json"):
                continue
            if path.stat().st_size > 20_000_000 or path in seen:
                continue
            seen.add(path)
            yield path


def load(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(payload, dict) or "contract" not in payload:
        return None
    return payload


def headline(payload: dict[str, Any]) -> str:
    for key in HEADLINE:
        if key in payload:
            value = payload[key]
            if isinstance(value, (str, int, float)):
                return f"{key}={value}"
            return f"{key}={json.dumps(value, default=str)[:110]}"
    return ""


def collect() -> list[dict[str, Any]]:
    rows = []
    for path in artifacts():
        payload = load(path)
        if payload is None:
            continue
        run_dir = path.parent
        fingerprint = ""
        decisive = run_dir / "decisive.json"
        summary = run_dir / "result.json"
        if summary.is_file():
            try:
                fingerprint = json.loads(summary.read_text()).get("fingerprint", "")
            except (json.JSONDecodeError, OSError):
                pass
        rows.append({
            "contract": payload["contract"],
            "path": str(path.relative_to(ROOT)),
            "run_dir": str(run_dir.relative_to(ROOT)),
            "modified": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "question": payload.get("question", ""),
            "claim_boundary": payload.get("claim_boundary", ""),
            "supersedes": payload.get("supersedes", ""),
            "method": payload.get("method", ""),
            "held_fixed": payload.get("held_fixed", []),
            "moving": payload.get("moving", ""),
            "headline": headline(payload),
            "traced": (run_dir / "trace.jsonl").is_file(),
            "has_decisive": decisive.is_file(),
            "fingerprint": fingerprint,
            "missing": [f for f in REQUIRED if not payload.get(f)],
        })
    rows.sort(key=lambda r: (r["contract"], r["modified"]), reverse=True)
    return rows


def newest_per_contract(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        current = seen.get(row["contract"])
        if current is None or row["modified"] > current["modified"]:
            seen[row["contract"]] = row
    return sorted(seen.values(), key=lambda r: r["modified"], reverse=True)


def render(rows: list[dict[str, Any]]) -> str:
    latest = newest_per_contract(rows)
    duplicates = {r["contract"] for r in rows} & {
        c for c in (r["contract"] for r in rows)
        if sum(1 for x in rows if x["contract"] == c) > 1}

    lines = [
        "# Experiment results",
        "",
        "Generated by `experiments/registry.py --write`. Do not edit by hand;",
        "edit the artifact's own `question` and `claim_boundary` instead.",
        "",
        "One row per contract, newest run. A contract is a stable id with a",
        "version: bump the version when a rerun changes what the number means,",
        "and set `supersedes` on the replacement so the withdrawn measurement",
        "stays visible.",
        "",
        f"{len(latest)} contracts, {len(rows)} artifacts, "
        f"{sum(1 for r in latest if r['traced'])} with a full trace.",
        "",
        "| Contract | Question | Headline | Traced | Artifact |",
        "|---|---|---|---|---|",
    ]
    for row in latest:
        question = (row["question"] or "—").replace("\n", " ")
        if len(question) > 96:
            question = question[:93] + "…"
        lines.append(
            f"| `{row['contract']}` | {question} | {row['headline'][:60] or '—'} "
            f"| {'yes' if row['traced'] else 'no'} | `{row['path']}` |")

    boundaries = [r for r in latest if r["claim_boundary"]]
    if boundaries:
        lines += ["", "## What each result does not support", ""]
        for row in boundaries:
            lines.append(f"**`{row['contract']}`** — "
                         + row["claim_boundary"].replace("\n", " "))
            lines.append("")

    superseded = [r for r in latest if r["supersedes"]]
    if superseded:
        lines += ["## Withdrawn and replaced", ""]
        for row in superseded:
            lines.append(f"- `{row['contract']}` supersedes {row['supersedes']}")
        lines.append("")

    incomplete = [r for r in latest if r["missing"]]
    if incomplete:
        lines += ["## Artifacts not following the convention", "",
                  "Each is missing a field the catalogue needs. Fix the script "
                  "that writes it, not this file.", ""]
        for row in incomplete:
            lines.append(f"- `{row['contract']}` missing "
                         f"{', '.join(row['missing'])} — `{row['path']}`")
        lines.append("")

    if duplicates:
        lines += ["## Contracts with more than one run", "",
                  "Expected when a script is rerun on the same inputs. A "
                  "problem when the inputs changed and the version did not.", ""]
        for contract in sorted(duplicates):
            runs = [r for r in rows if r["contract"] == contract]
            lines.append(f"- `{contract}` — {len(runs)} runs, newest "
                         f"{runs[0]['modified']}")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="refresh RESULTS.md and the index")
    ap.add_argument("--lint", action="store_true", help="exit non-zero on convention breaks")
    args = ap.parse_args()

    rows = collect()
    latest = newest_per_contract(rows)

    if args.write:
        CATALOGUE.write_text(render(rows))
        INDEX.write_text(json.dumps(
            {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "contracts": len(latest), "artifacts": len(rows),
             "results": rows}, indent=2, ensure_ascii=False) + "\n")
        print(f"{CATALOGUE.relative_to(ROOT)}  {len(latest)} contracts")
        print(f"{INDEX.relative_to(ROOT)}  {len(rows)} artifacts")
        return 0

    print(f"{'contract':46s} {'modified':17s} {'traced':6s} headline")
    for row in latest:
        print(f"{row['contract']:46s} {row['modified']:17s} "
              f"{'yes' if row['traced'] else 'no':6s} {row['headline'][:58]}")
    print(f"\n{len(latest)} contracts across {len(rows)} artifacts")

    if args.lint:
        broken = [r for r in latest if r["missing"]]
        for row in broken:
            print(f"  MISSING {', '.join(row['missing'])}: {row['path']}")
        print(f"\n{len(broken)} artifacts missing required fields")
        return 1 if broken else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
