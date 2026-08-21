"""Answer each context arm and score it — the behaviour layer, no GPU required.

This is the layer that has to find an effect before any mechanism work is worth
running. It takes the arms built by `make_context_arms.py`, asks the model once
per arm, and scores the answers blind.

Relation to what already exists — this generalises, it does not duplicate.
`scripts/pattern_traces/ab_reasoning.py` (#495) established the design this
follows: two arms given the SAME facts in different form, so the measurement is
about representation rather than information access. `port1_finbench.py` scaled
it to FinBench planted gold. Both are hardcoded to two arms
(`baseline` / `seocho`) and neither has a structure control. This runs FOUR — the
third form (`both`) and the structure control (`graph_unstructured`) — over an arbitrary
arms file, and reuses their deterministic token checker rather than inventing a
scorer.

What it does NOT yet take from them: the multi-turn loop and `turns_to_correct`,
which is a richer signal than a binary and worth adopting once the single-turn
pass shows an effect worth resolving.

Design choices that are not incidental:

  deterministic first  a gold answer that is a short string is checked by token
                       containment, exactly as `ab_reasoning._check_search` does.
                       No judge, no judge variance, no cost. The LLM judge is the
                       fallback for prose golds (GraphRAG-Bench), and every row
                       records which scorer ran.
  synthesis != judge   when the judge is used at all: MiniMax-M2.7 answers,
                       gpt-oss-120b judges. Following ADR-0105, which held the
                       synthesis model constant and scored with a different one,
                       so a model cannot mark its own homework.
  blind judging        the judge never sees which arm produced an answer, nor the
                       other arms' answers. Arm identity is the independent
                       variable; letting the judge see it is how a preference
                       becomes a result.
  raw answers kept     every response is written out, so scoring can be redone
                       with a different judge without paying for generation twice.
  temperature 0        both roles. Reasoning-token count still varies run to run,
                       but the answer should not.
  refusal is an answer the absence stratum (S5, and ERB's Info Not Found) is
                       *supposed* to produce "not stated". That is scored correct
                       against a gold of "none", not as a failure to answer.

The prompt is deliberately thin. Any instruction richer than "answer from the
context" starts doing the work the context form is supposed to be doing, and the
comparison stops being about the context.

Usage:
    export MARA_API_KEY=...      # strip the quotes .env wraps it in
    python scripts/serve_track/run_arms.py \\
        --arms outputs/serve_track/arms.jsonl \\
        --out outputs/serve_track/results.jsonl --limit 20
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
ARMS = ("vector", "graph", "both", "graph_unstructured")

_ANSWER_SYSTEM = (
    "Answer the question using only the context provided. "
    "If the context does not contain the answer, reply exactly: NOT STATED. "
    "Be brief."
)

# The same instruction minus the refusal licence. Needed because the prompt
# above ANSWERS the question ERB's `info_not_found` stratum asks: told to say
# NOT STATED when the context falls short, every arm said it, 20/20, and the run
# measured the instruction rather than the context form. Whether a form makes a
# model invent an answer can only be seen when refusing has not been pre-
# authorised. Constant across arms either way, so within a run the comparison
# holds; across runs the condition must be stated, which is why it is a flag and
# not a silent default.
_ANSWER_SYSTEM_NO_HINT = (
    "Answer the question using only the context provided. Be brief."
)

_JUDGE_SYSTEM = (
    "You grade one answer against a reference. Reply with exactly one word: "
    "CORRECT if the answer conveys the reference, otherwise WRONG. "
    "Ignore wording, ordering and formatting; judge the substance. "
    "When the reference is a LIST, the answer must name every item in it and "
    "must not name any item that is not in it — an extra item is WRONG, not a "
    "harmless detail. "
    "If the reference is 'none' or says information is absent, then a reply "
    "stating the information is not present is CORRECT."
)


def _client(base_url: str, api_key: str):
    from openai import OpenAI

    return OpenAI(base_url=base_url, api_key=api_key)


def _ask(client, model: str, system: str, user: str, max_tokens: int) -> Dict[str, Any]:
    started = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.0,
        max_tokens=max_tokens,
    )
    usage = resp.usage
    message = resp.choices[0].message
    text = (message.content or "").strip()
    if not text:
        # Reasoning models can put everything in the reasoning field when the
        # budget runs out. Treat that as an empty answer, not a crash.
        extra = getattr(message, "model_extra", None) or {}
        text = str(extra.get("reasoning_content") or extra.get("reasoning") or "").strip()
    return {
        "text": text,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "usage": usage.model_dump() if hasattr(usage, "model_dump") else {},
    }


def answer_arm(client, model: str, question: str, context: str, max_tokens: int,
               system: str = _ANSWER_SYSTEM) -> Dict[str, Any]:
    user = f"Context:\n{context}\n\nQuestion: {question}"
    return _ask(client, model, system, user, max_tokens)


def _normalise(text: str) -> str:
    """Fold case and collapse every kind of whitespace to a single ASCII space.

    Not cosmetic. The first run scored a correct `Model K1` as wrong because the
    model emitted U+202F NARROW NO-BREAK SPACE between the two words. A containment
    check that is literal about invisible characters silently under-counts, and the
    under-count lands on whichever arm happens to format more prettily — which is
    the arm identity we are trying to measure.
    """
    return " ".join(unicodedata.normalize("NFKC", text or "").split()).lower()


def check_deterministic(gold: str, candidate: str) -> Optional[bool]:
    """Token containment, the `ab_reasoning._check_search` rule. None = not applicable.

    Only used where the gold is short enough to be a fact rather than prose; a
    containment test over a paragraph would pass on coincidence.

    A LIST gold defers to the judge, and that is not a convenience. The negation
    stratum's golds are sets — "Model K2, Model M3, Model R4" — and containment
    scored every correct answer wrong because the model wrote "K2, M3, and R4"
    or listed them in a different order. Worse, containment cannot see the error
    that matters: an answer naming the three gold items PLUS two that do not
    belong is wrong, and a substring test calls it right. Set membership alone
    does not fix that either, since the excluded universe is not in this file.
    So list golds go to the judge, which is told the reference and can weigh
    both omissions and additions.
    """
    gold = (gold or "").strip()
    if not gold or len(gold) > 60:
        return None
    if "," in gold:
        return None  # handled by check_set, which needs the complement
    # A SENTENCE gold cannot be checked by containment even when it is short.
    # GraphRAG-Bench answers are full sentences: gold "Rubber boots are worn on
    # the feet." is 33 characters, passed the length test, and scored the reply
    # "feet" wrong — 31 of 32 items failed this way, which reads as a model
    # collapse and was a scorer artefact. Containment is for VALUE golds, the
    # entity names and counts the synthetic set uses; anything sentence-shaped
    # goes to the judge, which can see that "feet" conveys the reference.
    if gold.endswith(".") or len(gold.split()) > 3:
        return None
    hay = _normalise(candidate)
    if gold.lower() in ("none", "not stated", "no answer"):
        return "not stated" in hay or "not present" in hay or "none" in hay
    return _normalise(gold) in hay


def check_set(gold: str, excluded: List[str], candidate: str) -> Optional[bool]:
    """Score a list answer as a set: every gold item present, no excluded one.

    Neither of the obvious alternatives works. Substring containment fails on
    ordering and on the word "and" — it scored all three correct negation
    answers wrong. An LLM judge told to reject extra items over-rejects instead,
    marking "K2, M3 and R4 are not sold in Norland" wrong because the reply went
    on to say where each IS sold; that is supporting detail, not a fourth item.

    The complement comes from the generator, which knows the closed world, so
    the check is exact in both directions: omitting a gold item fails, and
    naming an item that does belong to Norland fails.
    """
    items = [g.strip() for g in (gold or "").split(",") if g.strip()]
    if len(items) < 2 or not excluded:
        return None
    hay = _normalise(candidate)
    if any(_normalise(item) not in hay for item in items):
        return False
    return not any(_normalise(bad) in hay for bad in excluded)


# Refusal is detected by pattern family, not by a list of strings. The string
# list this replaces missed "does not INCLUDE" while catching "does not
# CONTAIN", and missed "cannot answer" while catching "cannot be answered" —
# scoring four correct refusals as inventions and inverting the result. Any
# enumeration of surface forms will keep losing to paraphrase; the productive
# constructions are finite and are matched here instead.
_REFUSAL_PATTERNS = tuple(re.compile(p) for p in (
    r"\b(?:does|do|did|is|are|was|were)\s+not\s+"
    r"(?:contain|include|mention|specify|provide|state|list|have|appear)",
    # Contractions are a separate branch, not an afterthought: "I don't have
    # information about X" was the last form the pattern set missed.
    r"\b(?:don't|doesn't|didn't|isn't|aren't|wasn't|weren't)\s+"
    r"(?:contain|include|mention|specify|provide|state|list|have|appear)",
    r"\b(?:cannot|can\s*not|can't|could\s+not|couldn't|unable\s+to)\s+"
    r"(?:answer|determine|find|identify|tell|be\s+answered|be\s+determined)",
    r"\bnot\s+(?:stated|specified|mentioned|provided|available|present|"
    r"included|listed|documented|found|given|answerable|fully\s+answerable)\b",
    r"\bno\s+(?:information|details|specifics?|mention|record|data)\b",
    r"\binsufficient\s+(?:information|context|detail)",
    r"\bnot\s+in\s+the\s+(?:provided\s+)?(?:context|documents?)\b",
))


def check_refusal(candidate: str) -> bool:
    """Did the answer decline, rather than invent one?

    ERB's `info_not_found` gold is not a fact — it is a 469-character
    instruction reading "the answer must state at some point that the query is
    not fully answerable". Handing that to an LLM judge would put the one
    measurement this run exists for behind the component that already
    over-rejected three correct answers elsewhere. Declining is a surface
    property of the reply, so it is matched directly.

    Generous by design: a false CORRECT understates the invention rate, which is
    the conservative direction for a claim that a context form makes models
    invent answers.

    Known recall limit, stated rather than tuned away. On ERB's 20 items every
    arm scored 19 or 20, and reading each non-match showed all of them were
    refusals in wording the patterns miss ("I cannot confirm whether ..."). The
    residual one-item spread between arms is detector recall, not behaviour.
    Widening the patterns until the arms separate would be manufacturing the
    result, so the spread is reported as noise and the stratum as a null.
    """
    hay = _normalise(candidate)
    return any(p.search(hay) for p in _REFUSAL_PATTERNS)


# A reasoning judge needs room to reason before it can answer. At max_tokens=8
# gpt-oss-120b spent the whole budget thinking and never emitted a verdict: the
# reply came back as "We need to compare answer", leaked reasoning text that
# `startswith("CORRECT")` rejects. Every judge-scored item in every run before
# this was therefore marked wrong, which on the GraphRAG-Bench sample looked
# like all four arms collapsing to 1 of 32.
_JUDGE_MAX_TOKENS = 512


def judge(client, model: str, question: str, gold: str, candidate: str) -> bool:
    """Blind: the judge is told the question, the reference and one answer. Never the arm.

    The verdict is searched for rather than required at position zero, because a
    reasoning model emits it after its reasoning and an unparseable reply must
    be visibly wrong rather than silently WRONG.
    """
    if not candidate:
        return False
    user = (f"Question: {question}\nReference answer: {gold}\n"
            f"Answer to grade: {candidate}")
    verdict = _ask(client, model, _JUDGE_SYSTEM, user, _JUDGE_MAX_TOKENS)["text"].upper()
    if "CORRECT" in verdict and "INCORRECT" not in verdict:
        return True
    if "WRONG" in verdict or "INCORRECT" in verdict:
        return False
    # Neither token present: the judge did not answer. Count it wrong, but say so.
    print(f"    WARN unparseable judge verdict: {verdict[:60]!r}")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("outputs/serve_track/results.jsonl"))
    parser.add_argument("--base-url", default="https://api.cloud.mara.com/v1")
    parser.add_argument("--model", default="MiniMax-M2.7")
    parser.add_argument("--judge-model", default="gpt-oss-120b")
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--limit", type=int, default=0, help="0 = all comparable items")
    parser.add_argument("--no-refusal-hint", action="store_true",
                        help="drop 'reply NOT STATED if absent' from the prompt. Required "
                             "for any hallucination measurement; see _ANSWER_SYSTEM_NO_HINT")
    args = parser.parse_args()

    key = os.environ.get("MARA_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        raise SystemExit("MARA_API_KEY is not set")
    client = _client(args.base_url, key)

    answer_system = _ANSWER_SYSTEM_NO_HINT if args.no_refusal_hint else _ANSWER_SYSTEM
    if args.no_refusal_hint:
        print("prompt condition: NO refusal hint (hallucination measurement)")

    rows = [json.loads(line) for line in args.arms.read_text(encoding="utf-8").splitlines() if line.strip()]
    items = [r for r in rows if r.get("comparable")]
    if args.limit:
        items = items[: args.limit]
    print(f"{len(items)} comparable items x {len(ARMS)} arms = {len(items) * len(ARMS)} generations")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, Any]] = []
    with args.out.open("w", encoding="utf-8") as handle:
        for index, item in enumerate(items, start=1):
            record: Dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "id": item.get("id"),
                "question": item["question"],
                "gold": item["answer"],
                "excluded": item.get("excluded") or [],
                "strata": item.get("strata", {}),
                "prompt_condition": "no_refusal_hint" if args.no_refusal_hint else "default",
                "budget_unit": item.get("budget_unit"),
                "arms": {},
            }
            for arm in ARMS:
                context = item["arms"][arm]["context"]
                try:
                    got = answer_arm(client, args.model, item["question"], context,
                                     args.max_tokens, system=answer_system)
                    if (item.get("strata") or {}).get("answer_type") == "info_not_found":
                        deterministic = check_refusal(got["text"])
                    else:
                        deterministic = check_deterministic(item["answer"], got["text"])
                    if deterministic is None:
                        deterministic = check_set(
                            item["answer"], item.get("excluded") or [], got["text"]
                        )
                    if deterministic is None:
                        correct = judge(client, args.judge_model, item["question"],
                                        item["answer"], got["text"])
                        scorer = "judge"
                    else:
                        correct = deterministic
                        scorer = "deterministic"
                    record["arms"][arm] = {
                        "answer": got["text"][:600],
                        "correct": correct,
                        "scorer": scorer,
                        "latency_ms": got["latency_ms"],
                        "usage": got["usage"],
                        "context_used": item["arms"][arm]["used"],
                        "error": None,
                    }
                except Exception as exc:  # noqa: BLE001 — one failed call must not lose the run
                    record["arms"][arm] = {
                        "answer": "", "correct": False, "error": f"{type(exc).__name__}: {exc}",
                        "context_used": item["arms"][arm]["used"],
                    }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            results.append(record)
            marks = "".join("o" if record["arms"][a]["correct"] else "." for a in ARMS)
            print(f"  [{index}/{len(items)}] {record['strata'].get('stratum','?'):18s} {marks}", flush=True)

    _report(results)


def _report(results: List[Dict[str, Any]]) -> None:
    print(f"\n=== overall (n={len(results)}) ===")
    for arm in ARMS:
        ok = sum(1 for r in results if r["arms"][arm]["correct"])
        errs = sum(1 for r in results if r["arms"][arm].get("error"))
        print(f"  {arm:15s} {ok}/{len(results)} = {ok/max(len(results),1)*100:5.1f}%"
              + (f"   ({errs} errors)" if errs else ""))

    by_stratum: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for row in results:
        by_stratum[row["strata"].get("stratum", "?")].append(row)

    print("\n=== by stratum — this is the table that matters ===")
    header = "  " + "stratum".ljust(20) + "n   " + "  ".join(a[:6].rjust(6) for a in ARMS)
    print(header)
    for stratum in sorted(by_stratum):
        rows = by_stratum[stratum]
        cells = []
        for arm in ARMS:
            ok = sum(1 for r in rows if r["arms"][arm]["correct"])
            cells.append(f"{ok}/{len(rows)}".rjust(6))
        print("  " + stratum.ljust(20) + str(len(rows)).ljust(4) + "  ".join(cells))

    _report_cost(results)

    print("\n`graph` vs `graph_unstructured` is the structure test: identical facts in")
    print("identical order, markup only. `graph` vs `vector` measures compression, not")
    print("a fair accuracy contest — read it against the token table above.")


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _report_cost(results: List[Dict[str, Any]]) -> None:
    """Print the serving cost of each arm, in the unit the engine actually bills.

    This exists because the first run buried it. `usage` was written to the file
    and never read, so the compression headline was quoted in CHARACTERS — where
    the graph form looked 5.9x smaller — while in prompt tokens, the thing prefill
    is paid in, the same arms were only 1.9x apart. Triples are punctuation-dense
    and tokenize badly; prose does not. A ratio nobody prints is a ratio nobody
    checks, so it is printed next to the accuracy table.

    TTFT is shown because it is the only prefill signal an API exposes, and with
    its spread because over a network it carries queueing and transport that have
    nothing to do with context length. Read the spread before believing the gap.
    """
    print("\n=== serving cost — the unit the engine bills in ===")
    print(f"  {'arm':16s}{'prompt_tok':>11s}{'output_tok':>11s}{'reason_tok':>11s}"
          f"{'TTFT_med':>10s}{'TTFT_sd':>9s}")
    means: Dict[str, float] = {}
    for arm in ARMS:
        usages = [r["arms"][arm].get("usage") or {} for r in results]
        prompt = [u.get("prompt_tokens") for u in usages if u.get("prompt_tokens")]
        out = [u.get("completion_tokens") for u in usages if u.get("completion_tokens")]
        reason = [(u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
                  for u in usages]
        ttft = [u["time_to_first_token"] * 1000 for u in usages if u.get("time_to_first_token")]
        if not prompt:
            continue
        means[arm] = sum(prompt) / len(prompt)
        spread = 0.0
        if len(ttft) > 1:
            mean_t = sum(ttft) / len(ttft)
            spread = (sum((x - mean_t) ** 2 for x in ttft) / (len(ttft) - 1)) ** 0.5
        print(f"  {arm:16s}{means[arm]:11.1f}{sum(out)/max(len(out),1):11.1f}"
              f"{sum(reason)/max(len(reason),1):11.1f}{_median(ttft):9.0f}ms{spread:8.0f}ms")

    if means.get("graph") and means.get("vector"):
        ratio = means["vector"] / means["graph"]
        print(f"\n  prompt-token ratio vector/graph = {ratio:.2f}x")
        print("  Quote THIS, not the character ratio. Characters are not what prefill costs.")


if __name__ == "__main__":
    main()
