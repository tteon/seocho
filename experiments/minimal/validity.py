#!/usr/bin/env python3
"""Two checks the headline result cannot be stated without.

The agreement numbers say no-ontology 0.375, hand-written 0.221, FIBO 0.193.
Before any of that is a result, two things have to hold, and neither was
checked.

Was the treatment delivered?
    An experiment that varies a schema has to show the schema reached the
    extractor and changed what it produced. If the model ignored the class list,
    every condition is the same condition and the comparison measures nothing.
    Read from the traces: did the prompt actually carry the class list, and do
    the labels that came back belong to it.

Is the difference larger than the noise?
    Sixteen cases is a small sample and the conditions differ by as little as
    eighteen keys. A bootstrap over cases gives an interval for each condition
    and for each pairwise difference, so a gap can be called a result or called
    a coin flip. Resampling is by case, not by fact, because facts within a case
    are not independent — one document's extraction succeeds or fails together.

Everything here reads what is already on disk. No model call, no cost.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(HERE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from dotenv import dotenv_values  # noqa: E402

for _key, _value in dotenv_values(ROOT / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)

import arms as arms_mod  # noqa: E402
import parallel  # noqa: E402

URI = "bolt://localhost:7687"
OUT_ROOT = ROOT / "outputs/minimal"
MODELS = ("deepseek", "gptoss", "minimax27")
INFRA = {"Document", "Chunk", "Version", "DocumentVersion", "Section",
         "__Memory__", "Memory"}


def auth() -> tuple[str, str]:
    return (os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", ""))


def normalize_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


# --------------------------------------------------------------------------
# Manipulation check


def prompt_evidence(run_dir: Path) -> dict[str, Any]:
    """What the extraction prompts in one run actually contained.

    The trace records the first 400 characters of every system prompt. That is
    enough to see whether a class list was present and roughly how long the
    schema block was, which is what the check needs.
    """
    trace = run_dir / "trace.jsonl"
    if not trace.is_file():
        return {}
    lengths: list[int] = []
    samples: list[str] = []
    for line in trace.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("stage") == "llm.extract.request":
            lengths.append(int(record.get("system_chars", 0)))
            if len(samples) < 3:
                samples.append(record.get("system_head", "")[:200])
    if not lengths:
        return {}
    return {"extraction_calls": len(lengths),
            "system_prompt_chars_min": min(lengths),
            "system_prompt_chars_max": max(lengths),
            "system_prompt_chars_mean": round(sum(lengths) / len(lengths), 1),
            "samples": samples}


def label_conformance(driver, arm: str, tag: str,
                      declared: set[str], cases: list[str]) -> dict[str, Any]:
    """Do the labels that came back belong to the schema that went in?

    Perfect conformance would be suspicious in the other direction — the
    pipeline writes undeclared labels without complaint, so a high share means
    the model followed the list rather than that the store enforced it.
    """
    def read(model: str) -> dict[str, int]:
        database = (f"arm{tag}{arm.lower()}{model}" if tag
                    else f"arm{arm.lower()}{model}")
        counts: dict[str, int] = defaultdict(int)
        with driver.session(database=database) as session:
            for case in cases:
                workspace = (f"arm{tag}-{arm.lower()}-{model}-{case}" if tag
                             else f"arm{arm.lower()}-{model}-{case}")
                for row in session.run(
                        "MATCH (n {_workspace_id:$w}) UNWIND labels(n) AS l "
                        "RETURN l AS label, count(*) AS c", w=workspace).data():
                    if row["label"] not in INFRA:
                        counts[row["label"]] += int(row["c"])
        return dict(counts)

    merged: dict[str, int] = defaultdict(int)
    for result in parallel.io_map(read, list(MODELS)):
        for label, count in (result or {}).items():
            merged[label] += count
    total = sum(merged.values())
    inside = sum(c for l, c in merged.items() if l in declared)
    return {
        "nodes": total,
        "declared_labels_used": len({l for l in merged if l in declared}),
        "declared_labels_available": len(declared),
        "conformance": round(inside / total, 4) if total else 0.0,
        "undeclared_labels": sorted(
            ((l, c) for l, c in merged.items() if l not in declared),
            key=lambda kv: -kv[1])[:10],
    }


# --------------------------------------------------------------------------
# Bootstrap


def rate_from(per_case: dict[str, tuple[int, int]], cases: list[str]) -> float:
    comparable = sum(per_case[c][0] for c in cases if c in per_case)
    keys = sum(per_case[c][1] for c in cases if c in per_case)
    return comparable / keys if keys else 0.0


def bootstrap(per_case: dict[str, dict[str, tuple[int, int]]],
              cases: list[str], draws: int, seed: int) -> dict[str, Any]:
    """Resample cases with replacement; report the interval for each condition
    and for each pairwise difference.

    The pairwise interval is the one that matters. Two conditions can have
    overlapping intervals and still differ reliably, because the same resampled
    cases are used for both — the difference is paired and its interval is
    narrower than the two separately would suggest.
    """
    rng = random.Random(seed)
    arms = sorted(per_case)
    draws_by_arm: dict[str, list[float]] = {a: [] for a in arms}
    diffs: dict[tuple[str, str], list[float]] = {
        pair: [] for pair in combinations(arms, 2)}
    for _ in range(draws):
        sample = [rng.choice(cases) for _ in cases]
        rates = {a: rate_from(per_case[a], sample) for a in arms}
        for arm, rate in rates.items():
            draws_by_arm[arm].append(rate)
        for left, right in diffs:
            diffs[(left, right)].append(rates[left] - rates[right])

    def interval(values: list[float]) -> tuple[float, float]:
        ordered = sorted(values)
        lo = ordered[int(0.025 * len(ordered))]
        hi = ordered[min(len(ordered) - 1, int(0.975 * len(ordered)))]
        return round(lo, 4), round(hi, 4)

    per_arm = {}
    for arm, values in draws_by_arm.items():
        lo, hi = interval(values)
        per_arm[arm] = {"point": round(rate_from(per_case[arm], cases), 4),
                        "low": lo, "high": hi}
    pairwise = {}
    for (left, right), values in diffs.items():
        lo, hi = interval(values)
        crosses_zero = lo <= 0 <= hi
        pairwise[f"{left}-{right}"] = {
            "difference": round(per_arm[left]["point"] - per_arm[right]["point"], 4),
            "low": lo, "high": hi,
            "separated": not crosses_zero,
        }
    return {"per_condition": per_arm, "pairwise": pairwise, "draws": draws}


def collect_per_case(driver, arms: list[str], tag: str,
                     cases: list[str]) -> dict[str, dict[str, tuple[int, int]]]:
    """(comparable, total) keys per case per condition, the bootstrap's input."""
    def read(job: tuple[str, str]) -> tuple[str, str, list[dict]]:
        arm, model = job
        database = (f"arm{tag}{arm.lower()}{model}" if tag
                    else f"arm{arm.lower()}{model}")
        rows = []
        with driver.session(database=database) as session:
            for case in cases:
                workspace = (f"arm{tag}-{arm.lower()}-{model}-{case}" if tag
                             else f"arm{arm.lower()}-{model}-{case}")
                data = session.run(
                    "MATCH (n {_workspace_id:$w}) WHERE n.name IS NOT NULL "
                    "RETURN labels(n) AS labels, n.name AS name", w=workspace).data()
                rows.append({"case": case,
                             "names": {normalize_name(r["name"]) for r in data
                                       if not (set(r["labels"]) & INFRA)}})
        return arm, model, rows

    jobs = [(arm, model) for arm in arms for model in MODELS]
    by_arm_case: dict[str, dict[str, dict[str, set[str]]]] = defaultdict(
        lambda: defaultdict(dict))
    for result in parallel.io_map(read, jobs):
        if result is None:
            continue
        arm, model, rows = result
        for row in rows:
            by_arm_case[arm][row["case"]][model] = row["names"]

    per_case: dict[str, dict[str, tuple[int, int]]] = {}
    for arm, cases_map in by_arm_case.items():
        counts: dict[str, tuple[int, int]] = {}
        for case, per_model in cases_map.items():
            seen: dict[str, int] = defaultdict(int)
            for names in per_model.values():
                for name in names:
                    seen[name] += 1
            total = len(seen)
            comparable = sum(1 for v in seen.values() if v >= 2)
            counts[case] = (comparable, total)
        per_case[arm] = counts
    return per_case


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="", help="'' for the first sweep, 'v2' for the second")
    ap.add_argument("--arms", default="A,B,C,D")
    ap.add_argument("--draws", type=int, default=5000)
    ap.add_argument("--class-limit", type=int, default=70)
    args = ap.parse_args()

    import observe
    from neo4j import GraphDatabase

    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]
    partial_dir = (ROOT / "outputs/evaluation/mdm_fedcat" /
                   f"log2026-reextract-{args.tag or 'v1'}")
    cases = sorted({json.loads(p.read_text())["case_id"]
                    for p in partial_dir.glob("*.json")})
    if not cases:
        raise SystemExit(f"no partials under {partial_dir}")

    run = observe.Run(OUT_ROOT, "validity", {"decisive": {
        "arms": arms, "models": list(MODELS), "tag": args.tag or "v1",
        "cases": cases, "bootstrap_draws": args.draws,
        "resample_unit": "case", "seed": 42}})

    with run.stage("declared", class_limit=args.class_limit) as out:
        fibo = arms_mod.parse_fibo()
        documents, _ = arms_mod.load_corpus_text()
        scoped = arms_mod.scope_to_corpus(fibo, documents, args.class_limit, 20)
        by_iri = {c["iri"]: c["node"] for c in scoped}
        frequency = arms_mod.document_frequency(
            documents,
            {arms_mod.normalize(b["label"])
             for b in fibo["object_properties"].values()
             if arms_mod.normalize(b["label"])})
        relations = arms_mod.scope_relations(fibo, by_iri, frequency, 40)
        declared_of = {
            "A": set(arms_mod.build_arm_a()["ontology"].nodes),
            "B": set(arms_mod.build_arm_b()["ontology"].nodes),
            "C": set(arms_mod.build_fibo_arm(scoped, relations)["ontology"].nodes),
            "D": set(arms_mod.build_fibo_arm(scoped, relations,
                                             synonyms=True)["ontology"].nodes),
        }
        declared_of["E"] = declared_of["C"]
        out["declared"] = {a: len(s) for a, s in declared_of.items()}

    driver = GraphDatabase.driver(URI, auth=auth())
    try:
        with run.stage("manipulation.prompt") as out:
            newest = sorted((ROOT / "outputs/minimal").glob("*-reextract"),
                            reverse=True)
            prompt = {}
            for directory in newest:
                prompt = prompt_evidence(directory)
                if prompt:
                    prompt["run_dir"] = str(directory.relative_to(ROOT))
                    break
            out.update({k: v for k, v in prompt.items() if k != "samples"})

        conformance = {}
        with run.stage("manipulation.labels", arms=arms) as out:
            for arm in arms:
                conformance[arm] = label_conformance(
                    driver, arm, args.tag, declared_of[arm], cases)
                out[f"{arm}_conformance"] = conformance[arm]["conformance"]
                out[f"{arm}_labels_used"] = (
                    f"{conformance[arm]['declared_labels_used']}/"
                    f"{conformance[arm]['declared_labels_available']}")

        with run.stage("bootstrap", draws=args.draws, unit="case") as out:
            per_case = collect_per_case(driver, arms, args.tag, cases)
            stats = bootstrap(per_case, cases, args.draws, 42)
            for arm, cell in stats["per_condition"].items():
                out[f"{arm}"] = f"{cell['point']:.3f} [{cell['low']:.3f}, {cell['high']:.3f}]"
            out["separated_pairs"] = sum(1 for v in stats["pairwise"].values()
                                         if v["separated"])
    finally:
        driver.close()

    payload = {
        "contract": "log2026.validity.v1",
        "question": ("Was the schema actually delivered to the extractor, and "
                     "are the differences between conditions larger than "
                     "sampling noise?"),
        "method": ("Manipulation check from the run traces and the labels that "
                   "came back; percentile bootstrap over cases with "
                   f"{args.draws} draws, resampling cases rather than facts "
                   "because facts inside one case are not independent"),
        "claim_boundary": ("The interval covers sampling variability across "
                           "these 16 cases only. It does not cover variation "
                           "between models, between runs of one model, or the "
                           "choice of matching rule."),
        "cases": len(cases), "models": list(MODELS), "tag": args.tag or "v1",
        "prompt_evidence": prompt,
        "label_conformance": conformance,
        "bootstrap": stats,
    }
    (run.dir / "validity.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print()
    print("manipulation check — did the schema reach the extractor?")
    if prompt:
        print(f"  extraction prompts {prompt['system_prompt_chars_min']}–"
              f"{prompt['system_prompt_chars_max']} chars "
              f"({prompt['extraction_calls']} calls)")
    for arm in arms:
        cell = conformance[arm]
        print(f"  condition {arm}: {cell['declared_labels_used']}/"
              f"{cell['declared_labels_available']} declared classes used, "
              f"{cell['conformance']:.1%} of nodes carry one")
    print("\nbootstrap over cases — is the difference real?")
    for arm, cell in stats["per_condition"].items():
        print(f"  {arm}  {cell['point']:.3f}  [{cell['low']:.3f}, {cell['high']:.3f}]")
    print()
    for pair, cell in stats["pairwise"].items():
        verdict = "separated" if cell["separated"] else "overlaps zero"
        print(f"  {pair:8s} {cell['difference']:+.3f}  "
              f"[{cell['low']:+.3f}, {cell['high']:+.3f}]  {verdict}")

    run.finish({"separated_pairs": sum(1 for v in stats["pairwise"].values()
                                       if v["separated"]),
                "conformance": {a: conformance[a]["conformance"] for a in arms},
                "artifact": str((run.dir / "validity.json").relative_to(ROOT))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
