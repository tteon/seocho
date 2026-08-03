#!/usr/bin/env python3
"""Part 2 — answering under five evidence conditions, one factor, five levels.

Registered in experiments/preregistration/2026-08-02-answering.md before any
call. The registration fixes the conditions, the gating ladder, the metric and
the judge subsample; this file records the two decisions the registration left
open, both made before any result existed:

1. WHICH VIEW A GRAPH CONDITION SERVES. Each sweep produced three graphs per
   case, one per extractor. The evidence handed over is the UNION of the three
   views, each fact tagged with the view that holds it. The passages condition
   is the full gold references — retrieval at its best — so the graph condition
   gets extraction at its best: everything any view captured (union coverage
   0.476 of gold-answer figures against 0.389 for the best single view,
   log2026.routing_ceiling.s1). Cross-view disagreement is left in, because it
   is real content of the condition, not noise on top of it. Any dedup rule
   would import Part 1's alignment machinery into Part 2's evidence.

2. WHAT THE SERIALIZER DROPS. The indexing pipeline attaches Document, Chunk,
   DocumentVersion and Section nodes, and Document/Chunk carry the reference
   passages verbatim. Serializing them would hand the graph conditions the
   passages condition's evidence and void the contrast, so those four labels
   and every property that is pipeline bookkeeping are excluded. The same
   exclusion applies to graph A and graph C — same code path, same ordering —
   so the A/C contrast is content, never formatting.

The anchors condition attaches POINTERS only — passage index and character
offset — never the source window's text. AN-H4 claims anchors change
attribution and not accuracy; shipping window text would carry answer content
and unmake the condition. The attribution checker (not the model) reads the
window from the corpus.

Failures are failures: a model call that errors is recorded and scored as
unanswered, reported as N attempted vs N scored (CLAUDE.md §20.2). Partials
are written per (condition, model, case) with the prompt hash, so re-running
the same command resumes instead of re-spending (CLAUDE.md §13).

    python3 experiments/answering.py --tag an1 --dry-run
    python3 experiments/answering.py --tag an1 --conditions closed_book,passages
    python3 experiments/answering.py --tag an1 --conditions graph_a,graph_c,graph_c_anchors
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/minimal"))

from dotenv import dotenv_values  # noqa: E402

for _key, _value in dotenv_values(ROOT / ".env").items():
    if _value is not None:
        os.environ.setdefault(_key, _value)

import observe  # noqa: E402
import provenance  # noqa: E402

OUT_ROOT = ROOT / "outputs/minimal"
PARTIAL_ROOT = ROOT / "outputs/evaluation/answering"
SNAP_ROOT = ROOT / "snapshots"

MODELS = {
    "deepseek": "DeepSeek-V3.1",
    "gptoss": "gpt-oss-120b",
    "minimax27": "MiniMax-M2.7",
}

CONDITIONS = ("closed_book", "passages", "graph_a", "graph_c",
              "graph_c_anchors")

# Which sweep each graph condition reads. an1 (frozen): graph A from s1,
# graph C from s2. The arithmetic supplement (an2) serves both from s3 via
# --graph-a-tag/--graph-c-tag; the condition letters never change.
GRAPH_SOURCE = {"graph_a": ("s1", "A"), "graph_c": ("s2", "C"),
                "graph_c_anchors": ("s2", "C")}

# Pipeline plumbing, not extraction output. Document and Chunk hold the
# reference passages verbatim; serializing any of these hands the graph
# conditions the passages condition's evidence.
PLUMBING_LABELS = {"Document", "Chunk", "DocumentVersion", "Section"}

# Node properties that are pipeline bookkeeping rather than extracted content.
BOOKKEEPING = {"status", "source_id", "memory_id", "workspace_id", "id",
               "category", "updated_at", "linked_id", "source_type"}

JUDGE_SUBSAMPLE = 60  # drawn once by seed 42, recorded in the run config

SYSTEM_PROMPT = (
    "You answer questions about SEC filings. The questions are terse expert "
    "queries; answer them as well as the evidence allows rather than asking "
    "for clarification. Answer concisely and state every figure exactly, "
    "with its unit and scale (e.g. '$1,906,715 thousand'). Only if the "
    "evidence contains nothing relevant, say 'cannot determine'.")

SYSTEM_PROMPT_CLOSED = (
    "You answer questions about SEC filings from your own knowledge. The "
    "questions are terse expert queries; answer them as well as you can "
    "rather than asking for clarification. Answer concisely and state every "
    "figure exactly, with its unit and scale. Only if you know nothing "
    "relevant, say 'cannot determine'.")

CITE_INSTRUCTION = (
    "\nEvery figure in the evidence carries a source pointer like [p0@656] "
    "(passage index @ character offset). When your answer uses a figure, "
    "cite its pointer immediately after the figure.")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- cases ----

def load_cases() -> dict[str, dict[str, Any]]:
    spec = importlib.util.spec_from_file_location(
        "finder_index", ROOT / "examples/mdm/11_index_providers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = {c["case_id"]: c for c in module.load_cases_full(seed=42)}

    # The dataset's own `type` column, for the prose/numeric strata. The
    # loader drops it, so it is re-read rather than re-derived.
    import pandas as pd
    df = pd.read_parquet(module.SOURCE_PARQUET, columns=["_id", "type"])
    types = dict(zip(df["_id"].astype(str), df["type"].astype(str)))
    for cid, case in cases.items():
        case["type"] = types.get(cid, "None")
    return cases


def sweep_case_ids(tag: str, letter: str) -> dict[str, dict[str, Path]]:
    """case_id -> {model_key: snapshot path} for one sweep and condition."""
    out: dict[str, dict[str, Path]] = {}
    root = SNAP_ROOT / tag
    if not root.exists():
        return out
    pattern = re.compile(
        rf"^{letter}_({'|'.join(MODELS)})_([0-9a-f]{{8}})\.jsonl$")
    for path in sorted(root.iterdir()):
        matched = pattern.match(path.name)
        if matched:
            out.setdefault(matched.group(2), {})[matched.group(1)] = path
    return out


def load_anchor_index(tag: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    """(model, case, eid) -> anchor record, for the pointer decoration."""
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    path = SNAP_ROOT / tag / "anchors.jsonl"
    if not path.exists():
        return index
    for line in path.open():
        record = json.loads(line)
        if record.get("kind") == "anchor":
            index[(record["model"], record["case"], record["eid"])] = record
    return index


# ------------------------------------------------------------ serializer ----

def serialize_graph(views: dict[str, Path],
                    anchors: dict[tuple[str, str, str], dict[str, Any]] | None,
                    case_id: str) -> str:
    """One case's union-of-views subgraph as text. Same code path for A and C.

    Nodes: label, then every non-bookkeeping property the extractor filled.
    Edges: name -TYPE-> name. Plumbing labels are dropped entirely, and edges
    touching them with them.
    """
    blocks: list[str] = []
    for model_key in sorted(views):
        names: dict[str, str] = {}
        node_lines: list[str] = []
        edge_lines: list[str] = []
        for line in views[model_key].open():
            record = json.loads(line)
            if record["kind"] == "node":
                labels = [l for l in record["labels"]
                          if l not in PLUMBING_LABELS]
                if not labels:
                    continue
                props = record["props"]
                name = str(props.get("name", "")).strip()
                if not name:
                    continue
                names[record["eid"]] = name
                parts = [f"({labels[0]}) {name}"]
                for key in sorted(props):
                    if key in BOOKKEEPING or key.startswith("_") or key == "name":
                        continue
                    value = str(props[key]).strip()
                    if value:
                        parts.append(f"{key}={value}")
                if anchors is not None:
                    hit = anchors.get((model_key, case_id, record["eid"]))
                    if hit:
                        parts.append(f"source=[p{hit['passage']}@{hit['offset']}]")
                node_lines.append("  " + " | ".join(parts))
            elif record["kind"] == "edge":
                source = names.get(record["source"])
                target = names.get(record["target"])
                if source and target:
                    edge_lines.append(
                        f"  {source} -{record['type']}-> {target}")
        blocks.append(f"[view: {MODELS[model_key]}]\nnodes:\n"
                      + "\n".join(node_lines) + "\nrelationships:\n"
                      + "\n".join(edge_lines))
    return "\n\n".join(blocks)


def build_prompt(condition: str, case: dict[str, Any],
                 graph_text: str | None) -> tuple[str, str]:
    """(system, user) — identical across conditions except the evidence block."""
    question = case["query"]
    if condition == "closed_book":
        return SYSTEM_PROMPT_CLOSED, f"Question: {question}"
    if condition == "passages":
        evidence = "\n\n".join(
            f"[passage {i}]\n{text}"
            for i, text in enumerate(case["references"]))
        header = "Evidence — filing passages:"
    else:
        evidence = graph_text or ""
        header = ("Evidence — knowledge graph extracted from the filing, as "
                  "three independent views (they may disagree; weigh them):")
    system = SYSTEM_PROMPT
    if condition == "graph_c_anchors":
        system = SYSTEM_PROMPT + CITE_INSTRUCTION
    user = f"{header}\n{evidence}\n\nQuestion: {question}"
    return system, user


# --------------------------------------------------------------- scoring ----
#
# The 4-call smoke caught two tokenizer defects before the first scored run,
# both fixed here rather than in provenance.tokenize — the anchors were
# located with that tokenizer and changing it would silently move Part 1:
#   - "SP 800-171" read "-171" as a negative number, the same
#     hyphen-adjacency defect check_narrative had; a "-" after an
#     alphanumeric is a joiner, not a sign
#   - "(1) ... (6)" enumeration markers counted as figures; a bare integer
#     0-20 wrapped in its own parentheses is a list marker, not a quantity
# Both rules apply to gold and produced text symmetrically, so they cannot
# favour a condition.

_JOINED_MINUS = re.compile(r"[0-9A-Za-z]-\d")
_LIST_MARKER = re.compile(r"\(\s*\d{1,2}\s*\)")


def scoring_figures(text: str) -> list[float]:
    """Numeric tokens for the overlap metric, scale-applied, deduplicated."""
    body = str(text or "")
    body = _LIST_MARKER.sub(" ", body)
    # Break alphanumeric-joined hyphens so "800-171" yields 800 and 171,
    # never -171. Symmetric, and it keeps real negatives like "-171 million".
    body = re.sub(r"(?<=[0-9A-Za-z])-(?=\d)", " ", body)
    figures: list[float] = []
    for token in provenance.tokenize([body]):
        if not any(provenance.close(token.scaled, f) for f in figures):
            figures.append(token.scaled)
    return figures


def gold_figures(answer: str) -> list[float]:
    return scoring_figures(answer)


def number_overlap(answer_text: str, gold: list[float]) -> float | None:
    """Share of the gold answer's figures the produced answer states.

    None when the gold answer has no figures — those cases are a separate
    stratum (the dataset's `type` column), never zeros deflating the mean.
    """
    if not gold:
        return None
    produced = scoring_figures(answer_text)
    hit = sum(1 for f in gold
              if any(provenance.close(f, p) for p in produced))
    return hit / len(gold)


CITATION = re.compile(r"\[p(\d+)@(\d+)\]")
WINDOW = 120


def attribution_check(answer_text: str,
                      references: list[str]) -> dict[str, int]:
    """A cited figure is attributed when the pointer's window contains it.

    Mechanical: the figure immediately preceding each citation is looked up
    in the cited passage within ±WINDOW characters of the cited offset.
    """
    served = 0
    attributed = 0
    for match in CITATION.finditer(answer_text):
        preceding = answer_text[max(0, match.start() - 80):match.start()]
        tokens = provenance.tokenize([preceding])
        if not tokens:
            continue
        served += 1
        figure = tokens[-1].scaled
        passage_index, offset = int(match.group(1)), int(match.group(2))
        if passage_index >= len(references):
            continue
        body = str(references[passage_index])
        window = body[max(0, offset - WINDOW):offset + WINDOW]
        if any(provenance.close(figure, t.scaled)
               for t in provenance.tokenize([window])):
            attributed += 1
    return {"figures_cited": served, "attributed": attributed}


# ---------------------------------------------------------------- runner ----

def partial_path(tag: str, condition: str, model_key: str,
                 case_id: str) -> Path:
    return PARTIAL_ROOT / tag / condition / model_key / f"{case_id}.json"


def answer_one(backend: Any, run: observe.Run, condition: str,
               model_key: str, case: dict[str, Any],
               system: str, user: str) -> dict[str, Any]:
    prompt_hash = sha(system + "\x00" + user)
    started = time.perf_counter()
    run._append({"stage": "llm.answer.request", "status": "sent",
                 "condition": condition, "model_key": model_key,
                 "case": case["case_id"], "prompt_hash": prompt_hash,
                 "system_chars": len(system), "user_chars": len(user)})
    try:
        response = backend.complete(system=system, user=user, temperature=0.0)
        text = (getattr(response, "content", None)
                or getattr(response, "text", "") or "")
        status = "ok"
        error = None
    except Exception as exc:  # noqa: BLE001 — a failure is a result (§20.2)
        text, status, error = "", "failed", repr(exc)
    elapsed = round(time.perf_counter() - started, 3)
    usage = {}
    if status == "ok":
        usage = getattr(response, "usage", None) or {}
        if hasattr(usage, "__dict__"):
            usage = dict(usage.__dict__)
    run._append({"stage": "llm.answer.response", "status": status,
                 "condition": condition, "model_key": model_key,
                 "case": case["case_id"], "seconds": elapsed,
                 "answer_head": text[:400], "error": error,
                 "usage": {k: v for k, v in dict(usage).items()
                           if isinstance(v, (int, float))}})

    gold = gold_figures(case["expected_answer"])
    record = {
        "case": case["case_id"], "condition": condition,
        "model_key": model_key, "model": MODELS[model_key],
        "category": case["category"], "type": case["type"],
        "prompt_hash": prompt_hash, "status": status, "error": error,
        "seconds": elapsed, "answer": text,
        "gold_figures": len(gold),
        "number_overlap": (number_overlap(text, gold)
                           if status == "ok" else None),
    }
    if condition == "graph_c_anchors" and status == "ok":
        record["attribution"] = attribution_check(text, case["references"])
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="an1")
    parser.add_argument("--conditions", default=",".join(CONDITIONS))
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--limit", type=int, default=0,
                        help="first N cases only, for a smoke run")
    parser.add_argument("--judge-subsample", type=int, default=JUDGE_SUBSAMPLE,
                        help="size of the seed-42 judge draw (an1: 60, "
                             "an2: 30 per its registration)")
    parser.add_argument("--graph-a-tag", default="s1",
                        help="sweep tag serving graph_a (an2 uses s3)")
    parser.add_argument("--graph-c-tag", default="s2",
                        help="sweep tag serving graph_c and its anchors "
                             "(an2 uses s3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="assemble and validate every prompt, call nothing")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    wanted_conditions = [c for c in args.conditions.split(",") if c]
    unknown = set(wanted_conditions) - set(CONDITIONS)
    assert not unknown, f"unknown conditions: {unknown}"
    wanted_models = [m for m in args.models.split(",") if m]
    assert set(wanted_models) <= set(MODELS)

    cases = load_cases()
    s1_views = sweep_case_ids(args.graph_a_tag, "A")
    s2_views = sweep_case_ids(args.graph_c_tag, "C")
    case_ids = sorted(s1_views)
    assert case_ids, "no s1 snapshots — run export_snapshots --tag s1 first"
    if args.limit:
        case_ids = case_ids[:args.limit]

    judge_draw = sorted(random.Random(42).sample(
        sorted(s1_views), min(args.judge_subsample, len(s1_views))))

    config = {
        "contract": f"log2026.answering.{args.tag}",
        "conditions": wanted_conditions, "models": wanted_models,
        "cases": len(case_ids), "graph_view_rule": "union of all three views",
        "plumbing_labels_dropped": sorted(PLUMBING_LABELS),
        "anchor_payload": "pointer only, never window text",
        "judge_subsample": judge_draw,
        "system_prompt_hash": sha(SYSTEM_PROMPT),
        "temperature": 0.0, "seed": 42, "tag": args.tag,
        "dry_run": args.dry_run,
    }
    config["decisive"] = {k: config[k] for k in
                          ("contract", "conditions", "models",
                           "graph_view_rule", "anchor_payload",
                           "system_prompt_hash", "temperature", "seed", "tag")}
    run = observe.Run(OUT_ROOT, "answering", config)

    anchor_index = load_anchor_index(args.graph_c_tag)

    # Assemble evidence once per (condition, case); shared across models.
    graph_cache: dict[tuple[str, str], str | None] = {}

    def graph_for(condition: str, case_id: str) -> str | None:
        key = (condition, case_id)
        if key not in graph_cache:
            source_tag, _ = GRAPH_SOURCE[condition]
            views = (s1_views if source_tag == "s1" else s2_views).get(case_id)
            if not views or len(views) < len(MODELS):
                graph_cache[key] = None
            else:
                graph_cache[key] = serialize_graph(
                    views,
                    anchor_index if condition == "graph_c_anchors" else None,
                    case_id)
        return graph_cache[key]

    with run.stage("assemble", conditions=wanted_conditions,
                   cases=len(case_ids)) as out:
        work: list[tuple[str, str, str, str, str]] = []
        missing: dict[str, int] = {}
        sizes: list[int] = []
        for condition in wanted_conditions:
            for case_id in case_ids:
                case = cases[case_id]
                graph_text = (graph_for(condition, case_id)
                              if condition in GRAPH_SOURCE else None)
                if condition in GRAPH_SOURCE and graph_text is None:
                    missing[condition] = missing.get(condition, 0) + 1
                    continue
                system, user = build_prompt(condition, case, graph_text)
                sizes.append(len(system) + len(user))
                for model_key in wanted_models:
                    work.append((condition, model_key, case_id, system, user))
        out["prompts"] = len(sizes)
        out["calls_planned"] = len(work)
        out["evidence_missing"] = missing
        out["prompt_chars"] = {
            "mean": int(statistics.mean(sizes)) if sizes else 0,
            "max": max(sizes) if sizes else 0}

    if args.dry_run:
        run.finish({"dry_run": True, "calls_planned": len(work),
                    "evidence_missing": missing})
        print(f"\ndry run: {len(work)} calls planned, nothing spent")
        for condition, count in missing.items():
            print(f"  {condition}: {count} cases lack complete evidence "
                  f"(all {len(MODELS)} views required)")
        return

    from seocho.store.llm import create_llm_backend
    backends = {m: create_llm_backend(provider="mara", model=MODELS[m])
                for m in wanted_models}

    def process(item: tuple[str, str, str, str, str]) -> dict[str, Any]:
        condition, model_key, case_id, system, user = item
        target = partial_path(args.tag, condition, model_key, case_id)
        if not args.no_resume and target.exists():
            existing = json.loads(target.read_text())
            if existing.get("prompt_hash") == sha(system + "\x00" + user):
                return {**existing, "resumed": True}
        record = answer_one(backends[model_key], run, condition, model_key,
                            cases[case_id], system, user)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1))
        tmp.replace(target)  # atomic, so a killed run never leaves half a file
        return record

    # One worker per model at a time would serialize needlessly; the pool is
    # shared and MARA's per-model limits bind long before args.workers does.
    results: list[dict[str, Any]] = []
    with run.stage("answer", calls=len(work)) as out:
        with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
            for record in pool.map(process, work):
                results.append(record)
                done = len(results)
                if done % 100 == 0:
                    run.log(f"{done}/{len(work)} answered")
        out["attempted"] = len(results)
        out["ok"] = sum(1 for r in results if r["status"] == "ok")
        out["failed"] = sum(1 for r in results if r["status"] != "ok")
        out["resumed"] = sum(1 for r in results if r.get("resumed"))

    summary: dict[str, Any] = {}
    for condition in wanted_conditions:
        for model_key in wanted_models:
            rows = [r for r in results if r["condition"] == condition
                    and r["model_key"] == model_key]
            scored = [r["number_overlap"] for r in rows
                      if r["number_overlap"] is not None]
            # The dataset's own `type` column marks the questions that need
            # arithmetic; the primary metric is at home there. type=None
            # gold answers are prose whose stray tokens are weaker targets,
            # so the two strata are reported apart (registration, scoring).
            numeric = [r["number_overlap"] for r in rows
                       if r["number_overlap"] is not None
                       and r["type"] != "None"]
            summary[f"{condition}/{model_key}"] = {
                "attempted": len(rows),
                "ok": sum(1 for r in rows if r["status"] == "ok"),
                "numeric_scored": len(scored),
                "number_overlap_mean": (round(statistics.mean(scored), 4)
                                        if scored else None),
                "numeric_stratum_n": len(numeric),
                "numeric_stratum_mean": (round(statistics.mean(numeric), 4)
                                         if numeric else None),
            }
    run.finish({"summary": summary})

    print(f"\n{'condition/model':38s} {'attempted':>9s} {'ok':>4s} "
          f"{'scored':>6s} {'overlap':>8s}")
    for key, row in summary.items():
        overlap = row["number_overlap_mean"]
        print(f"{key:38s} {row['attempted']:>9d} {row['ok']:>4d} "
              f"{row['numeric_scored']:>6d} "
              f"{overlap if overlap is not None else '—':>8}")


if __name__ == "__main__":
    main()
