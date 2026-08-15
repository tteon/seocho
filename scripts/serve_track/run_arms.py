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


def answer_arm(client, model: str, question: str, context: str, max_tokens: int) -> Dict[str, Any]:
    user = f"Context:\n{context}\n\nQuestion: {question}"
    return _ask(client, model, _ANSWER_SYSTEM, user, max_tokens)


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


def judge(client, model: str, question: str, gold: str, candidate: str) -> bool:
    """Blind: the judge is told the question, the reference and one answer. Never the arm."""
    if not candidate:
        return False
    user = (f"Question: {question}\nReference answer: {gold}\n"
            f"Answer to grade: {candidate}")
    verdict = _ask(client, model, _JUDGE_SYSTEM, user, 8)["text"].upper()
    return verdict.startswith("CORRECT")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("outputs/serve_track/results.jsonl"))
    parser.add_argument("--base-url", default="https://api.cloud.mara.com/v1")
    parser.add_argument("--model", default="MiniMax-M2.7")
    parser.add_argument("--judge-model", default="gpt-oss-120b")
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--limit", type=int, default=0, help="0 = all comparable items")
    args = parser.parse_args()

    key = os.environ.get("MARA_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        raise SystemExit("MARA_API_KEY is not set")
    client = _client(args.base_url, key)

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
                "budget_unit": item.get("budget_unit"),
                "arms": {},
            }
            for arm in ARMS:
                context = item["arms"][arm]["context"]
                try:
                    got = answer_arm(client, args.model, item["question"], context, args.max_tokens)
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
