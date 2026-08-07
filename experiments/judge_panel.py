#!/usr/bin/env python3
"""The answer-grading judge panel (LLM_JUDGE_PROTOCOL.md, 2026-08-05 addendum).

Cross-judging: each answer is graded by the two MARA models that did not
write it, plus Kimi. Calibration controls run first and a judge that fails
any control is excluded from that dimension, reported. Verdicts are JSON,
reference-anchored, blind to condition and model; citation markers are
stripped before grading.

    python3 experiments/judge_panel.py --calibrate
    python3 experiments/judge_panel.py --tag an1 --judges kimi
    python3 experiments/judge_panel.py --tag an1 --judges gptoss,minimax27,deepseek
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
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

PARTIAL_ROOT = ROOT / "outputs/evaluation/judge_panel"
ANSWER_ROOT = ROOT / "outputs/evaluation/answering"
CITATION = re.compile(r"\[p\d+@\d+\]")

MARA_MODELS = {"gptoss": "gpt-oss-120b", "minimax27": "MiniMax-M2.7",
               "deepseek": "DeepSeek-V3.1"}
CONDITIONS = ("closed_book", "passages", "graph_a", "graph_c",
              "graph_c_anchors")

SYSTEM = (
    "You grade one candidate answer against a gold answer for a question "
    "about SEC filings. The gold answer is authoritative: do not overrule "
    "it with your own financial knowledge, even if you believe it is wrong."
    "\n\nStep 0 — abstention check, before anything else: if the candidate "
    "declines to answer (e.g. says 'cannot determine', 'I don't know', or "
    "equivalent), the verdict is \"abstained\". Stop; do not fact-check a "
    "refusal.\n\nOtherwise follow these steps in order:\n"
    "1. From the gold answer, list the facts the question requires.\n"
    "2. For each required fact, check the candidate: match, missing, or "
    "wrong. A figure matches when it is the same quantity within 1%. "
    "Convert only when the stated units differ ($1.9 billion equals "
    "$1,906,715 thousand). If two figures carry the same stated unit, or "
    "no unit, and differ by a factor of 10, 1,000, or 1,000,000, that is "
    "a wrong figure, not a conversion.\n"
    "3. Only then choose the verdict, by this rubric:\n"
    "   - correct: every required fact matches\n"
    "   - partially_correct: at least one required fact matches and at "
    "least one is missing or wrong\n"
    "   - incorrect: the candidate contradicts the gold answer, or no "
    "required fact matches\n"
    "Length is not quality. Extra detail the question did not ask for "
    "neither helps nor hurts, unless it contradicts the gold answer.\n\n"
    'Return JSON only:\n{"required_facts": [{"fact": "...", '
    '"in_candidate": "match|missing|wrong"}],\n "verdict": '
    '"correct|partially_correct|incorrect|abstained",\n "rationale": '
    '"one sentence naming the decisive match or mismatch"}')

VERDICTS = ("correct", "partially_correct", "incorrect", "abstained")


def judge_draw(tag: str) -> list[str]:
    """The seed-42 subsample recorded in the answering run's config."""
    candidates = sorted(
        (ROOT / "outputs/minimal").glob("*-answering/config.resolved.json"),
        reverse=True)
    for path in candidates:
        config = json.loads(path.read_text())
        if (config.get("contract") == f"log2026.answering.{tag}"
                and config.get("judge_subsample")):
            return config["judge_subsample"]
    raise SystemExit(f"no judge subsample recorded for {tag}")


def load_answers(tag: str, case_ids: list[str]) -> list[dict[str, Any]]:
    rows = []
    wanted = set(case_ids)
    for cond in CONDITIONS:
        for author in MARA_MODELS:
            directory = ANSWER_ROOT / tag / cond / author
            if not directory.is_dir():
                continue
            for path in directory.glob("*.json"):
                record = json.loads(path.read_text())
                if record["case"] in wanted and record["status"] == "ok":
                    rows.append(record)
    return rows


def load_gold() -> dict[str, dict[str, str]]:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {c["case_id"]: {"question": c["query"],
                           "gold": c["expected_answer"]}
            for c in module.load_cases_full(seed=42)}


def backend_for(judge: str):
    from seocho.store.llm import create_llm_backend
    if judge == "kimi":
        return create_llm_backend(provider="kimi")
    return create_llm_backend(provider="mara", model=MARA_MODELS[judge])


def grade(backend, judge: str, question: str, gold: str,
          candidate: str) -> dict[str, Any]:
    user = (f"Question: {question}\n\nGold answer: {gold}\n\n"
            f"Candidate answer: {CITATION.sub('', candidate)}")
    kwargs = {} if judge == "kimi" else {"temperature": 0.0}
    response = backend.complete(system=SYSTEM, user=user, **kwargs)
    text = (getattr(response, "content", None)
            or getattr(response, "text", "") or "")
    matched = re.search(r"\{.*\}", text, re.S)
    payload = json.loads(matched.group(0)) if matched else {}
    verdict = payload.get("verdict")
    if verdict not in VERDICTS:
        raise ValueError(f"bad verdict {verdict!r}")
    return {"verdict": verdict,
            "rationale": str(payload.get("rationale", ""))[:200]}


def calibrate(judges: list[str], gold_map: dict) -> dict[str, Any]:
    """Four control groups with verdicts known by construction."""
    rng = random.Random(42)
    # Controls must be cases whose gold answer is figure-dense: corrupting
    # the numbers must corrupt the required facts. A prose answer citing
    # "ASC 450" survives x1000 corruption with its required facts intact,
    # and a judge calling it correct is right — that is a degenerate
    # control, not a judge failure (found in calibration v2, both judges
    # missing the same items).
    figure_dense = [cid for cid, payload in sorted(gold_map.items())
                    if len(re.findall(r"\$\s?\d[\d,\.]*",
                                      payload["gold"])) >= 3]
    case_ids = rng.sample(figure_dense, 10)
    other = {c: rng.choice([x for x in sorted(gold_map) if x != c])
             for c in case_ids}

    def corrupt(text: str) -> str:
        return re.sub(r"\d[\d,]*\.?\d*",
                      lambda m: str(_scale(m.group(0))), text)

    def _scale(token: str) -> str:
        try:
            return format(float(token.replace(",", "")) * 1000, ",.0f")
        except ValueError:
            return token

    controls = []
    for cid in case_ids:
        gold = gold_map[cid]["gold"]
        controls += [
            {"cid": cid, "kind": "gold_as_candidate", "candidate": gold,
             "must": {"correct"}},
            {"cid": cid, "kind": "swapped_answer",
             "candidate": gold_map[other[cid]]["gold"],
             "must": {"incorrect"}},
            {"cid": cid, "kind": "scale_corrupted",
             "candidate": corrupt(gold),
             "must": {"incorrect", "partially_correct"}},
            {"cid": cid, "kind": "refusal", "candidate": "cannot determine",
             "must": {"abstained"}},
        ]
    report: dict[str, Any] = {}
    for judge in judges:
        backend = backend_for(judge)
        outcomes = {}
        def one(control):
            try:
                got = grade(backend, judge, gold_map[control["cid"]]["question"],
                            gold_map[control["cid"]]["gold"],
                            control["candidate"])["verdict"]
            except Exception as exc:  # noqa: BLE001
                got = f"failed:{type(exc).__name__}"
            return control["kind"], got in control["must"], got
        with ThreadPoolExecutor(4) as pool:
            for kind, passed, got in pool.map(one, controls):
                outcomes.setdefault(kind, []).append((passed, got))
        report[judge] = {
            kind: {"passed": sum(1 for p, _ in rows if p),
                   "n": len(rows),
                   "misses": [g for p, g in rows if not p][:3]}
            for kind, rows in outcomes.items()}
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="an1")
    parser.add_argument("--judges", default="kimi")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    judges = [j for j in args.judges.split(",") if j]
    gold_map = load_gold()

    if args.calibrate:
        run = observe.Run(ROOT / "outputs/minimal", "judge-calibration", {
            "contract": "log2026.judge_calibration.v1",
            "decisive": {"judges": judges, "controls": 4, "cases": 10,
                         "seed": 42},
        })
        with run.stage("calibrate", judges=judges) as out:
            out["report"] = calibrate(judges, gold_map)
        run.finish({"report": out["report"]})
        print(json.dumps(out["report"], indent=1, ensure_ascii=False))
        return

    case_ids = judge_draw(args.tag)
    answers = load_answers(args.tag, case_ids)
    run = observe.Run(ROOT / "outputs/minimal", "judge-panel", {
        "contract": f"log2026.judge_panel.{args.tag}",
        "decisive": {"tag": args.tag, "judges": judges,
                     "subsample": len(case_ids), "seed": 42,
                     "cross_judging": "a judge never grades its own model",
                     "system_hash": observe.hashlib.sha256(
                         SYSTEM.encode()).hexdigest()[:16]},
    })
    with run.stage("grade", answers=len(answers), judges=judges) as out:
        counts = {"graded": 0, "skipped_self": 0, "failed": 0}
        for judge in judges:
            backend = backend_for(judge)
            todo = [a for a in answers
                    if judge == "kimi" or a["model_key"] != judge]
            counts["skipped_self"] += len(answers) - len(todo)

            def one(answer):
                target = (PARTIAL_ROOT / args.tag / judge
                          / answer["condition"] / answer["model_key"]
                          / f"{answer['case']}.json")
                if target.exists():
                    return "cached"
                try:
                    verdict = grade(backend, judge,
                                    gold_map[answer["case"]]["question"],
                                    gold_map[answer["case"]]["gold"],
                                    answer["answer"])
                except Exception as exc:  # noqa: BLE001
                    verdict = {"verdict": "failed",
                               "error": repr(exc)[:150]}
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".tmp")
                tmp.write_text(json.dumps(
                    {**verdict, "case": answer["case"],
                     "condition": answer["condition"],
                     "author": answer["model_key"], "judge": judge},
                    ensure_ascii=False))
                tmp.replace(target)
                return verdict["verdict"]
            with ThreadPoolExecutor(args.workers) as pool:
                for got in pool.map(one, todo):
                    counts["graded"] += 1
                    if got == "failed":
                        counts["failed"] += 1
        out.update(counts)
    run.finish({"counts": counts})
    print(counts)


if __name__ == "__main__":
    main()
