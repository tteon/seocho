"""A/B baseline: how a reasoning model works unaided vs with SEOCHO context.

The question this answers (hadry, 2026-08-15): capture how a reasoning model
(MiniMax-M2.7 first) thinks during *indexing* and *search*, then measure what
adding SEOCHO's ontology-aligned context changes — specifically **how many
turns it takes to reach a good result**.

Design:

- Two arms per case. ``baseline`` gets the task and the raw source text.
  ``seocho`` gets the same task and the same facts, but as SEOCHO would
  present them — an ontology schema (indexing) or ontology-aligned graph
  context (search). Same information, different representation: the arms
  differ in *form*, not in what is knowable, so turns-to-correct measures
  the representation, not information access.
- Multi-turn loop with a deterministic checker. Each turn the model answers;
  the checker scores against gold; on failure the model gets concrete
  feedback (what is missing/wrong) and tries again, up to ``--max-turns``.
  ``turns_to_correct`` is None when the budget runs out — reported, never
  imputed.
- Every turn is an LLMStep in a trace-schema v1 episode, so the corpus joins
  the fix.11 pattern traces and the reasoning-token accounting comes from
  the provider verbatim.

Usage:
  MARA_API_KEY=... python scripts/pattern_traces/ab_reasoning.py \
      --model MiniMax-M2.7 --repeats 2 --out outputs/pattern_traces/ab.jsonl
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

_spec = importlib.util.spec_from_file_location(
    "pattern_trace_schema", Path(__file__).resolve().parent / "schema.py")
schema = importlib.util.module_from_spec(_spec)
sys.modules["pattern_trace_schema"] = schema
_spec.loader.exec_module(schema)


# ---------------------------------------------------------------------------
# Cases. Raw text and graph context carry the SAME facts by construction.
# ---------------------------------------------------------------------------

_EMAIL = (
    "From: kyle@hanbit.example\n"
    "Please wire 48,000 USD from account 9000001 to account 9000002 today via "
    "cross-border wire. Tomorrow move 47,500 USD from 9000002 on to 9000003 the "
    "same way. Hanbit Trading fully owns account 9000003; sweep its balance of "
    "95,000 USD back to 9000001 through an internal transfer before the audit."
)

_GRAPH_CONTEXT = (
    "Account(9000001) -TRANSFER {amount: 48000, channel: WIRE_CROSSBORDER}-> Account(9000002)\n"
    "Account(9000002) -TRANSFER {amount: 47500, channel: WIRE_CROSSBORDER}-> Account(9000003)\n"
    "Company(Hanbit Trading) -OWN {share: 1.0}-> Account(9000003)\n"
    "Account(9000003) -TRANSFER {amount: 95000, channel: INTERNAL}-> Account(9000001)"
)

_ONTOLOGY_SCHEMA = (
    "Labels: Account(acct_no INTEGER UNIQUE), Company(name STRING UNIQUE)\n"
    "Relationships: TRANSFER(Account->Account, {amount, channel}), "
    "OWN(Company->Account, {share})\n"
    "Channels: WIRE_CROSSBORDER, INTERNAL"
)

# Indexing gold: entity identifiers and relationship triples that must appear.
_INDEXING_GOLD_ENTITIES = {"9000001", "9000002", "9000003", "Hanbit Trading"}
_INDEXING_GOLD_RELS = {
    ("9000001", "TRANSFER", "9000002"),
    ("9000002", "TRANSFER", "9000003"),
    ("Hanbit Trading", "OWN", "9000003"),
    ("9000003", "TRANSFER", "9000001"),
}

SEARCH_CASES = [
    {
        "id": "final_beneficiary",
        "question": "Which account ultimately receives the swept funds, and "
                    "through which company's account do they pass?",
        "gold_all": ["9000001", "Hanbit"],
    },
    {
        "id": "layering_total",
        "question": "Summing every cross-border wire mentioned (exclude internal "
                    "transfers), what total amount moved? Answer with the number.",
        "gold_all": ["95,500", "95500"],
        "gold_any": True,   # either formatting counts
    },
    {
        "id": "channel_of_return",
        "question": "Through which channel does money return to the originating "
                    "account?",
        "gold_all": ["INTERNAL"],
    },
]


def _check_search(case: Dict[str, Any], answer: str) -> Tuple[bool, str]:
    hay = answer.lower()
    tokens = case["gold_all"]
    if case.get("gold_any"):
        ok = any(t.lower() in hay for t in tokens)
        missing = [] if ok else tokens
    else:
        missing = [t for t in tokens if t.lower() not in hay]
        ok = not missing
    return ok, ("" if ok else f"missing from your answer: {missing}")


def _check_indexing(answer: str) -> Tuple[bool, str]:
    try:
        payload = json.loads(answer[answer.index("{"): answer.rindex("}") + 1])
    except Exception:
        return False, "reply was not parseable JSON with nodes/relationships"
    names = set()
    for node in payload.get("nodes", []):
        for value in (node.get("properties") or {}).values():
            names.add(str(value))
    rels = set()
    for rel in payload.get("relationships", []):
        rels.add((str(rel.get("source")), str(rel.get("type")), str(rel.get("target"))))
    missing_entities = _INDEXING_GOLD_ENTITIES - names
    missing_rels = _INDEXING_GOLD_RELS - rels
    if not missing_entities and not missing_rels:
        return True, ""
    return False, (f"missing entities: {sorted(missing_entities)}; "
                   f"missing relationships: {sorted(missing_rels)}")


# ---------------------------------------------------------------------------
# Arms: same facts, different representation
# ---------------------------------------------------------------------------

def build_prompt(pattern: str, arm: str, case: Dict[str, Any]) -> Tuple[str, str]:
    if pattern == "search":
        system = ("Answer the question from the provided material only. "
                  "Be precise; name identifiers exactly.")
        material = _GRAPH_CONTEXT if arm == "seocho" else _EMAIL
        prefix = "Graph context (ontology-aligned):" if arm == "seocho" else "Source text:"
        return system, f"{prefix}\n{material}\n\nQuestion: {case['question']}"
    # indexing
    if arm == "seocho":
        system = ("Extract a graph strictly following this ontology. Return one "
                  "JSON object {\"nodes\": [{label, properties}], \"relationships\": "
                  "[{type, source, target, properties}]} where source/target are "
                  "the identifying property values.\n" + _ONTOLOGY_SCHEMA)
    else:
        system = ("Extract entities and relationships from the text. Return one "
                  "JSON object {\"nodes\": [{label, properties}], \"relationships\": "
                  "[{type, source, target, properties}]}. Choose labels, types "
                  "and identifiers yourself.")
    return system, _EMAIL


def run_case(backend, *, pattern: str, arm: str, case: Dict[str, Any],
             model: str, max_turns: int, repeat: int) -> schema.Episode:
    system, user = build_prompt(pattern, arm, case)
    episode = schema.Episode(
        pattern=pattern, case_id=f"{case['id']}:{arm}:r{repeat}",
        model=model, provider="mara")
    episode.ontology = "aml_mini" if arm == "seocho" else ""
    messages_feedback: List[str] = []
    turns_to_correct = None

    for turn in range(1, max_turns + 1):
        turn_user = user
        if messages_feedback:
            turn_user += ("\n\nYour previous answer was not accepted. "
                          + messages_feedback[-1] + "\nAnswer again, corrected.")
        started = time.perf_counter()
        error = None
        try:
            response = backend.complete(system=system, user=turn_user,
                                        temperature=0.0, task_hint=pattern,
                                        mode="pipeline")
            text = response.text or ""
        except Exception as exc:
            response, text, error = None, "", f"{type(exc).__name__}: {exc}"
        latency_ms = (time.perf_counter() - started) * 1000.0

        if pattern == "search":
            ok, feedback = _check_search(case, text)
            parse = "not_applicable"
        else:
            ok, feedback = _check_indexing(text)
            parse = "ok" if ok or "parseable" not in feedback else "failed"

        episode.steps.append(schema.LLMStep(
            role=f"turn_{turn}", model=model, latency_ms=round(latency_ms, 1),
            usage=dict(getattr(response, "usage", {}) or {}),
            text_chars=len(text), parse=parse,
            prompt_sections={"system": len(system), "user": len(turn_user)},
            error=error))
        if ok:
            turns_to_correct = turn
            break
        messages_feedback.append(feedback)

    episode.outcome = {
        "arm": arm,
        "turns_to_correct": turns_to_correct,
        "turns_used": len(episode.steps),
        "total_completion_tokens": sum(
            int(step.usage.get("completion_tokens", 0) or 0)
            for step in episode.steps),
    }
    return episode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="mara")
    parser.add_argument("--model", default="MiniMax-M2.7")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--patterns", default="indexing,search")
    parser.add_argument("--out", default="outputs/pattern_traces/ab_reasoning.jsonl")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from seocho.store.llm import create_llm_backend

    backend = create_llm_backend(provider=args.provider, model=args.model)
    out = Path(args.out)
    patterns = [p.strip() for p in args.patterns.split(",")]

    summary: Dict[str, Dict[str, List[Any]]] = {}
    for pattern in patterns:
        cases = SEARCH_CASES if pattern == "search" else [{"id": "aml_email"}]
        for case in cases:
            for arm in ("baseline", "seocho"):
                for repeat in range(args.repeats):
                    episode = run_case(backend, pattern=pattern, arm=arm,
                                       case=case, model=args.model,
                                       max_turns=args.max_turns, repeat=repeat)
                    schema.append_episode(out, episode)
                    o = episode.outcome
                    summary.setdefault(f"{pattern}/{arm}", {"turns": [], "tokens": []})
                    summary[f"{pattern}/{arm}"]["turns"].append(o["turns_to_correct"])
                    summary[f"{pattern}/{arm}"]["tokens"].append(o["total_completion_tokens"])
                    print(f"{pattern:9s} {case['id']:18s} {arm:8s} r{repeat} "
                          f"turns={o['turns_to_correct']} "
                          f"tokens={o['total_completion_tokens']}", flush=True)

    print("\n=== summary (turns_to_correct; None = never within budget) ===")
    for key, values in sorted(summary.items()):
        turns = values["turns"]
        solved = [t for t in turns if t is not None]
        print(f"{key:20s} solved {len(solved)}/{len(turns)} "
              f"avg_turns={sum(solved)/len(solved):.2f} " if solved else
              f"{key:20s} solved 0/{len(turns)} ",
              f"avg_tokens={sum(values['tokens'])/len(values['tokens']):.0f}")


if __name__ == "__main__":
    main()
