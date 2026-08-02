#!/usr/bin/env python3
"""Write each fact's recovered source anchor beside the snapshots.

`provenance.py` can locate a figure in its source passage, but recomputing that
for every analysis is both slow and a chance for two analyses to disagree about
where a fact came from. This materialises it once as a derived layer that ships
with the snapshots:

    snapshots/<tag>/anchors.jsonl

One record per anchored fact, holding the condition, model, case, the name the
model gave it, the value it wrote, the passage and offset it came from, the
literal text at that offset, and the surrounding window a reader would be shown.

The scale ratio is carried explicitly. A figure that only matched after
rescaling has a ratio of a thousand or a million against the token it came from,
and that is the mis-reading the whole alignment argument turns on — a model
applying a table's "in thousands" header when its neighbours did not, or failing
to when they did. Recording the ratio makes those countable rather than
inferrable.

Facts with no unique anchor are counted, not written. They are the honest
denominator: attribution recovered after the fact cannot reach everything, and a
layer that quietly covered only what it could would overstate itself.

    python3 experiments/materialize_anchors.py --tag v2
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT / "experiments/minimal"), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import provenance  # noqa: E402

SNAPSHOTS = ROOT / "snapshots"
OUT_ROOT = ROOT / "outputs/minimal"
INFRA = {"Document", "Chunk", "Version", "DocumentVersion", "Section",
         "__Memory__", "Memory"}


def load_cases() -> dict[str, dict[str, Any]]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {c["case_id"]: c for c in module.load_cases_full(seed=42)}


def facts_in(path: Path) -> list[dict[str, Any]]:
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") != "node":
            continue
        labels = record.get("labels") or []
        if set(labels) & INFRA:
            continue
        props = record.get("props") or {}
        raw = props.get("value") or props.get("amount") or ""
        value = provenance.parse_amount(raw)
        if value is None:
            continue
        found.append({"eid": record.get("eid", ""),
                      "labels": sorted(l for l in labels if l not in INFRA),
                      "name": str(props.get("name", "")),
                      "raw": str(raw), "value": value,
                      "period": str(props.get("period", "") or "")})
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="v2")
    args = ap.parse_args()

    import observe

    directory = SNAPSHOTS / (args.tag or "v1")
    if not (directory / "manifest.json").is_file():
        raise SystemExit(f"no snapshots under {directory}")

    run = observe.Run(OUT_ROOT, "materialize-anchors", {"decisive": {
        "tag": args.tag,
        "rule": ("unique numeric token in the case's reference passages; exact "
                 "match preferred over rescaled; ambiguous figures unanchored"),
        "tolerance": 0.001, "seed": 42}})

    with run.stage("corpus") as out:
        cases = load_cases()
        out["cases_available"] = len(cases)

    output = directory / "anchors.jsonl"
    written = 0
    unanchored = 0
    ratios: Counter = Counter()
    per_condition: dict[str, dict[str, int]] = defaultdict(
        lambda: {"anchored": 0, "unanchored": 0, "rescaled": 0})

    with run.stage("anchor", snapshots=str(directory.relative_to(ROOT))) as out:
        with output.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "kind": "header",
                "contract": "log2026.fact_anchors.v1",
                "tag": args.tag,
                "note": ("recovered after extraction, not recorded during it; "
                         "see experiments/minimal/provenance.py for the rule "
                         "and its limits"),
            }, ensure_ascii=False) + "\n")

            for path in sorted(directory.glob("*.jsonl")):
                if path.name in ("anchors.jsonl",):
                    continue
                parts = path.stem.split("_")
                if len(parts) < 3:
                    continue
                arm, model, case = parts[0], parts[1], "_".join(parts[2:])
                references = cases.get(case, {}).get("references") or []
                if not references:
                    continue
                tokens = provenance.tokenize(references)
                for fact in facts_in(path):
                    anchor = provenance.locate(fact["value"], tokens)
                    if anchor is None:
                        unanchored += 1
                        per_condition[arm]["unanchored"] += 1
                        continue
                    ratio = round(anchor.scale_ratio, 6)
                    ratios[ratio] += 1
                    per_condition[arm]["anchored"] += 1
                    if not anchor.exact:
                        per_condition[arm]["rescaled"] += 1
                    fh.write(json.dumps({
                        "kind": "anchor", "arm": arm, "model": model,
                        "case": case, "eid": fact["eid"],
                        "labels": fact["labels"], "name": fact["name"],
                        "extracted": fact["raw"], "value": fact["value"],
                        "period": fact["period"],
                        "passage": anchor.passage, "offset": anchor.offset,
                        "literal": anchor.literal,
                        "source_value": anchor.source_value,
                        "exact": anchor.exact, "scale_ratio": ratio,
                        "window": provenance.window(references, anchor),
                    }, ensure_ascii=False) + "\n")
                    written += 1
        total = written + unanchored
        out["facts_with_a_figure"] = total
        out["anchored"] = written
        out["unanchored"] = unanchored
        out["anchor_rate"] = round(written / total, 4) if total else 0.0
        out["size_mb"] = round(output.stat().st_size / 1048576, 2)

    off_scale = {r: c for r, c in ratios.items() if abs(r - 1.0) > 1e-6}
    summary = {
        "contract": "log2026.fact_anchors_summary.v1",
        "question": ("Can a figure a model extracted be attributed to the place "
                     "in the source it came from, after the fact?"),
        "method": ("each extracted figure located against the numeric tokens of "
                   "its case's reference passages, searching the value as parsed "
                   "and at every scale; exact matches preferred, ambiguous "
                   "figures left unanchored"),
        "claim_boundary": ("An anchor is a unique numeric coincidence, not a "
                           "provenance record written during extraction. Two "
                           "unrelated facts sharing one figure would be "
                           "attributed to the same token if only one occurrence "
                           "exists. Only figures can be anchored at all, so "
                           "facts without one are outside this entirely."),
        "tag": args.tag,
        "facts_with_a_figure": written + unanchored,
        "anchored": written, "unanchored": unanchored,
        "anchor_rate": (round(written / (written + unanchored), 4)
                        if (written + unanchored) else 0.0),
        "by_condition": {k: dict(v) for k, v in sorted(per_condition.items())},
        "scale_ratios_other_than_one": dict(sorted(
            off_scale.items(), key=lambda kv: -kv[1])[:12]),
        "output": str(output.relative_to(ROOT)),
    }
    (run.dir / "anchors_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    print()
    print(f"{summary['anchored']:,} of {summary['facts_with_a_figure']:,} figures "
          f"anchored ({summary['anchor_rate']:.1%})")
    print(f"written to {output.relative_to(ROOT)}")
    print(f"\n{'cond':6s} {'anchored':>9s} {'unanchored':>11s} {'rescaled':>9s}")
    for arm, cell in summary["by_condition"].items():
        print(f"{arm:6s} {cell['anchored']:9d} {cell['unanchored']:11d} "
              f"{cell['rescaled']:9d}")
    if off_scale:
        print("\nfigures that only matched after rescaling — a model reading a "
              "table's units differently from its neighbours:")
        for ratio, count in sorted(off_scale.items(), key=lambda kv: -kv[1])[:8]:
            print(f"  x{ratio:<12g} {count}")

    run.finish({"anchored": written, "unanchored": unanchored,
                "anchor_rate": summary["anchor_rate"],
                "artifact": str((run.dir / "anchors_summary.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
