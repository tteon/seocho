#!/usr/bin/env python3
"""Multi-agent adjudication over anchored cross-view disagreements (MA-).

Registered in experiments/preregistration/2026-08-05-multiagent-adjudication.md
before any call. Ground truth per disagreement is the source token's value,
so every arm is scored mechanically. The adjudicator is Kimi K2.5, the one
model uninvolved in every extraction and every answer in this study.

    python3 experiments/disagreement_adjudication.py --pairs s1:A,s2:C --arms M0,M1,M2,M3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/minimal"))

from dotenv import dotenv_values  # noqa: E402

_env = dotenv_values(ROOT / ".env")
if _env.get("KIMI_API_KEY"):
    os.environ["MOONSHOT_API_KEY"] = _env["KIMI_API_KEY"]
for _k, _v in _env.items():
    if _v is not None and _k != "MOONSHOT_API_KEY":
        os.environ.setdefault(_k, _v)

import observe  # noqa: E402
import provenance  # noqa: E402

PARTIAL_ROOT = ROOT / "outputs/evaluation/adjudication_ma"
WINDOW = 120
DISAGREE = 0.01   # the provenance_keying convention: >1% relative spread

SYSTEM = (
    "Two independent extraction systems disagree about a numeric fact from "
    "an SEC filing. Decide the correct value. You may pick one of the "
    "candidates, output a corrected value, or abstain if it cannot be "
    "determined from what you see. State the value as a plain number with "
    "scale applied (e.g. 1906715000, not '$1,906,715 thousand').\n"
    'Return JSON only: {"value": <number or null>, '
    '"basis": "one short sentence"}')


def load_cases_refs() -> dict[str, list[str]]:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {c["case_id"]: c["references"]
            for c in module.load_cases_full(seed=42)}


def disagreements(tag: str, letter: str) -> list[dict[str, Any]]:
    """Groups of >=2 views anchored to one source token whose values differ."""
    groups: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for line in (ROOT / "snapshots" / tag / "anchors.jsonl").open():
        record = json.loads(line)
        if record.get("kind") != "anchor":
            continue
        key = (record["case"], record["passage"], record["offset"])
        groups[key][record["model"]] = record
    rows = []
    for (case, passage, offset), members in groups.items():
        if len(members) < 2:
            continue
        values = [m["value"] for m in members.values()]
        low, high = min(values), max(values)
        scale = max(abs(low), abs(high))
        if not scale or abs(high - low) / scale <= DISAGREE:
            continue
        any_member = next(iter(members.values()))
        rows.append({
            "id": f"{tag}-{letter}-{case}-{passage}-{offset}",
            "tag": tag, "condition": letter, "case": case,
            "passage": passage, "offset": offset,
            "source_value": any_member["source_value"],
            "views": {model: {"value": m["value"], "name": m["name"],
                              "extracted": m["extracted"],
                              "labels": m.get("labels", [])}
                      for model, m in members.items()},
        })
    return sorted(rows, key=lambda r: r["id"])


def build_prompt(arm: str, row: dict[str, Any],
                 refs: dict[str, list[str]]) -> str:
    lines = ["Disputed fact — the extractors' claims:"]
    for model, view in sorted(row["views"].items()):
        lines.append(f"  system {model}: name='{view['name']}', "
                     f"stated value={view['value']} "
                     f"(as written: '{view['extracted']}')")
    if arm == "M2":
        passages = refs.get(row["case"], [])
        if row["passage"] < len(passages):
            body = str(passages[row["passage"]])
            lo = max(0, row["offset"] - WINDOW)
            lines.append("\nSource document window around the disputed "
                         f"figure:\n  ...{body[lo:row['offset'] + WINDOW]}...")
    if arm == "M3":
        for model, view in sorted(row["views"].items()):
            labels = ", ".join(view.get("labels") or ["(none)"])
            lines.append(f"  declared type of {model}'s node: {labels}")
        lines.append("\n(No source text is available to you; only the "
                     "declared schema types above.)")
    lines.append("\nWhich value is correct?")
    return "\n".join(lines)


def score(picked: float | None, source: float) -> str:
    if picked is None:
        return "abstained"
    return "correct" if provenance.close(picked, source, 0.001) else "wrong"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", default="s1:A,s2:C")
    parser.add_argument("--arms", default="M0,M1,M2,M3")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    arms = [a for a in args.arms.split(",") if a]
    refs = load_cases_refs()
    rows: list[dict[str, Any]] = []
    for pair in args.pairs.split(","):
        tag, letter = pair.split(":")
        rows += disagreements(tag, letter)
    if args.limit:
        rows = rows[:args.limit]

    run = observe.Run(ROOT / "outputs/minimal", "ma-adjudication", {
        "contract": "log2026.ma_adjudication.v1",
        "decisive": {"pairs": args.pairs, "arms": arms, "window": WINDOW,
                     "disagreement_rule": f"relative spread > {DISAGREE}",
                     "adjudicator": "kimi-k2.5", "seed": 42,
                     "accuracy": "close() at 0.1% vs the source token"},
    })

    backend = None
    if not args.dry_run and any(a != "M0" for a in arms):
        from seocho.store.llm import create_llm_backend
        backend = create_llm_backend(provider="kimi")

    def adjudicate(arm: str, row: dict[str, Any]) -> dict[str, Any]:
        target = PARTIAL_ROOT / arm / f"{row['id']}.json"
        if target.exists():
            return json.loads(target.read_text())
        if arm == "M0":
            matching = [v["value"] for v in row["views"].values()
                        if provenance.close(v["value"], row["source_value"],
                                            0.001)]
            picked = matching[0] if matching else None
            record = {"id": row["id"], "arm": arm, "picked": picked,
                      "verdict": score(picked, row["source_value"]),
                      "basis": "anchor rule"}
        else:
            user = build_prompt(arm, row, refs)
            started = time.perf_counter()
            try:
                response = backend.complete(system=SYSTEM, user=user)
                text = (getattr(response, "content", None)
                        or getattr(response, "text", "") or "")
                matched = re.search(r"\{.*\}", text, re.S)
                payload = json.loads(matched.group(0)) if matched else {}
                picked = payload.get("value")
                picked = float(picked) if picked is not None else None
                record = {"id": row["id"], "arm": arm, "picked": picked,
                          "verdict": score(picked, row["source_value"]),
                          "basis": str(payload.get("basis", ""))[:200],
                          "seconds": round(time.perf_counter() - started, 2)}
            except Exception as exc:  # noqa: BLE001 — a failure is a result
                record = {"id": row["id"], "arm": arm, "picked": None,
                          "verdict": "failed", "error": repr(exc)[:200]}
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False))
        tmp.replace(target)
        return record

    result: dict[str, Any] = {"groups": len(rows)}
    for arm in arms:
        with run.stage(f"arm.{arm}", groups=len(rows)) as out:
            if args.dry_run:
                out["planned"] = len(rows)
                continue
            with ThreadPoolExecutor(args.workers) as pool:
                records = list(pool.map(lambda r: adjudicate(arm, r), rows))
            counts = {v: sum(1 for r in records if r["verdict"] == v)
                      for v in ("correct", "wrong", "abstained", "failed")}
            scored = counts["correct"] + counts["wrong"]
            out.update(counts)
            out["accuracy_of_decided"] = (round(counts["correct"] / scored, 4)
                                          if scored else None)
            out["coverage"] = round(scored / len(records), 4)
            result[arm] = {k: out[k] for k in
                           ("correct", "wrong", "abstained", "failed",
                            "accuracy_of_decided", "coverage")}

    artifact = {
        "contract": "log2026.ma_adjudication.v1",
        "question": ("When two extractors disagree on an anchored figure, "
                     "what resolves it: deliberation, provenance, schema, "
                     "or a rule with no LLM at all?"),
        "claim_boundary": (
            "Ground truth is the source token's value, so accuracy is "
            "mechanical; where every view mis-stated the value and the "
            "window is ambiguous, abstention is correct behavior, so "
            "accuracy is reported with coverage, never alone. One "
            "adjudicator model (Kimi K2.5); arm prompts share one scaffold "
            "and differ only in evidence."),
        **result,
    }
    (run.dir / "ma_adjudication.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=1))
    run.finish(result)
    for arm in arms:
        if isinstance(result.get(arm), dict):
            r = result[arm]
            print(f"{arm}: 정확도(판정분) {r['accuracy_of_decided']} "
                  f"커버리지 {r['coverage']} (correct {r['correct']}, "
                  f"wrong {r['wrong']}, abstain {r['abstained']}, "
                  f"failed {r['failed']})")


if __name__ == "__main__":
    main()
