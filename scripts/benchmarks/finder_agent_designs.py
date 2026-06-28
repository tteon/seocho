#!/usr/bin/env python3
"""5-archetype agent-design bake-off, PER-STEP instrumented (seocho-q8x/7fl).

Compares orchestration patterns by HOW they navigate the schema layer (which
FinDER categories to fetch) for cross-category B-questions, gated by the intent
router (#207). Every orchestration STEP emits a span (seocho.tracing JSONL, the
6q9.1 backend) tagged with archetype/step/latency/decision + a per-step
correctness signal, so we can attribute WHERE each archetype is good/bad — not
just the end metric.

PRIMARY = deterministic layer-routing (coverage of gold-required categories,
precision/over-fetch, cost=#LLM calls + per-step latency); token_f1 + cross-model
judge (gpt-oss-120b) secondary (LLM judge noisy, #206). Tenant isolation is
CIK-scoped here and proven live in #196.

Archetypes: single_shot / intent_gated (router->fetch->answer) / react (reason+
act tool loop) / reflexion (critique->re-fetch) / plan_multi (per-category
specialists->compose). MARA-first.

Run: PYTHONPATH=src:scripts/benchmarks python3 scripts/benchmarks/finder_agent_designs.py --n 0 --trace
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Set

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "src", _ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from finder_arms import ANSWER_SPEC, CONTEXT_BUDGET, answer, judge  # noqa: E402
from finder_arms_bquestion import build_bquestions  # noqa: E402
from finder_intent_router import CATEGORIES, _llm_route, route  # noqa: E402
from finder_judge import token_f1  # noqa: E402
from examples.finder.lib import llm_io  # noqa: E402
from seocho import tracing  # noqa: E402
from seocho.semantic_layer.identity import EntityResolver  # noqa: E402

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except Exception:
    pass

JUDGE_SPEC = "mara/gpt-oss-120b"
ROUTER_SPEC = "mara/gpt-oss-120b"

# per-step span accumulator (in-memory; also emitted to JSONL when --trace)
STEPS: List[dict] = []


@contextmanager
def span(step: str, archetype: str, bq_id: str, **meta):
    """Time one orchestration step, record it, and emit a tracing span."""
    rec = {"step": step, "archetype": archetype, "bq": bq_id, **meta}
    t0 = time.perf_counter()
    try:
        yield rec
    finally:
        rec["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        STEPS.append(rec)
        tracing.log_span(
            name=f"{archetype}.{step}",
            output_data={k: rec[k] for k in ("decision", "step_correct") if k in rec},
            metadata={"elapsed_ms": rec["elapsed_ms"], "bq": bq_id, "step": step},
            tags=[archetype, step],
        )


def _fetch(bq, cat: str) -> str:
    return "\n\n".join(bq.ev_by_cat.get(cat, []))


def _ctx(bq, cats) -> str:
    blocks = [f"[{c}]\n{_fetch(bq, c)}" for c in sorted(cats) if _fetch(bq, c)]
    return "\n\n".join(blocks)[:CONTEXT_BUDGET]


def _bqid(bq) -> str:
    return f"{bq.ticker}:{bq.cat_a}+{bq.cat_b}"


def _route_cats(bq, rc, rm, arch) -> Set[str]:
    with span("router", arch, _bqid(bq)) as s:  # the arbiter/schema-selection step
        cats = set(_llm_route(bq.query, rc, rm) or route(bq.query).required_categories)
        s["decision"] = sorted(cats)
        # step-correctness: did the gate pick exactly the gold-required categories?
        s["step_correct"] = cats == bq.required
        s["recall"] = len(cats & bq.required) / len(bq.required)
        s["precision"] = len(cats & bq.required) / len(cats) if cats else 0.0
    return cats


# --------------------------------------------------------------------------
# Archetypes
# --------------------------------------------------------------------------
def a_single_shot(bq, ac, am, rc, rm):
    fetched: Set[str] = set()
    with span("answer", "single_shot", _bqid(bq), llm=True):
        ans = answer(ac, am, bq.query, "")
    return ans, fetched, 1


def a_intent_gated(bq, ac, am, rc, rm):
    cats = _route_cats(bq, rc, rm, "intent_gated")
    with span("answer", "intent_gated", _bqid(bq), llm=True):
        ans = answer(ac, am, bq.query, _ctx(bq, cats))
    return ans, cats, 2


def a_react(bq, ac, am, rc, rm, max_steps: int = 3):
    seen: Dict[str, str] = {}
    calls = 0
    for _ in range(max_steps):
        with span("react.decide", "react", _bqid(bq), llm=True) as s:
            opts = ", ".join(c for c in CATEGORIES if c not in seen) or "(none)"
            sysmsg = (
                "You are answering a 10-K question. Decide the NEXT action. "
                f"Reply ONE token: a section to read [{opts}] or ANSWER if you "
                "have enough. Output only the token."
            )
            user = (
                f"Question: {bq.query}\nAlready read: {list(seen)}\n"
                f"Evidence so far:\n{chr(10).join(seen.values())[:1200]}"
            )
            r = ac.chat.completions.create(
                model=am,
                temperature=0,
                max_tokens=300,
                messages=[
                    {"role": "system", "content": sysmsg},
                    {"role": "user", "content": user},
                ],
            )
            calls += 1
            choice = (r.choices[0].message.content or "").upper()
            picked = next(
                (c for c in CATEGORIES if c.upper() in choice and c not in seen), None
            )
            still_needed = bq.required - set(seen)
            s["decision"] = picked or "ANSWER"
            # bad step = stopped (ANSWER) while a required category is still unread
            s["step_correct"] = not (picked is None and bool(still_needed))
        if picked is None:
            break
        seen[picked] = f"[{picked}]\n{_fetch(bq, picked)}"
    with span("answer", "react", _bqid(bq), llm=True):
        ans = answer(ac, am, bq.query, "\n\n".join(seen.values())[:CONTEXT_BUDGET])
    return ans, set(seen), calls + 1


def a_reflexion(bq, ac, am, rc, rm):
    cats = _route_cats(bq, rc, rm, "reflexion")
    calls = 1
    with span("answer", "reflexion", _bqid(bq), llm=True):
        ans = answer(ac, am, bq.query, _ctx(bq, cats))
        calls += 1
    with span("reflexion.critique", "reflexion", _bqid(bq), llm=True) as s:
        crit = (
            "Critique whether the answer fully addresses EVERY part of the "
            "question. Reply a comma list of additional sections needed from "
            f"[{', '.join(CATEGORIES)}], or NONE. Output only that."
        )
        r = ac.chat.completions.create(
            model=am,
            temperature=0,
            max_tokens=400,
            messages=[
                {"role": "system", "content": crit},
                {"role": "user", "content": f"Q: {bq.query}\nA: {ans}"},
            ],
        )
        calls += 1
        extra = {
            c
            for c in CATEGORIES
            if c.lower() in (r.choices[0].message.content or "").lower()
        }
        missing = bq.required - cats
        s["decision"] = sorted(extra)
        # good critique = it flagged a genuinely-missing required category (or none missing)
        s["step_correct"] = bool(extra & missing) if missing else not extra
    if extra - cats:
        cats |= extra
        with span("reflexion.reanswer", "reflexion", _bqid(bq), llm=True):
            ans = answer(ac, am, bq.query, _ctx(bq, cats))
            calls += 1
    return ans, cats, calls


def a_plan_multi(bq, ac, am, rc, rm):
    cats = _route_cats(bq, rc, rm, "plan_multi")
    calls = 1
    sub = []
    for c in sorted(cats):
        with span("plan.subanswer", "plan_multi", _bqid(bq), llm=True, cat=c):
            sub.append(f"[{c}] " + answer(ac, am, bq.query, _ctx(bq, {c})))
            calls += 1
    with span("plan.compose", "plan_multi", _bqid(bq), llm=True):
        compose = (
            "Compose a single answer from these per-section findings. Keep the numbers."
        )
        r = ac.chat.completions.create(
            model=am,
            temperature=0,
            max_tokens=900,
            messages=[
                {"role": "system", "content": compose},
                {"role": "user", "content": f"Q: {bq.query}\n\n" + "\n\n".join(sub)},
            ],
        )
        calls += 1
    return (r.choices[0].message.content or "").strip(), cats, calls


ARCHETYPES = {
    "single_shot": a_single_shot,
    "intent_gated": a_intent_gated,
    "react": a_react,
    "reflexion": a_reflexion,
    "plan_multi": a_plan_multi,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4, help="B-questions (0 = all 17)")
    ap.add_argument("--trace", action="store_true", help="emit per-step spans to JSONL")
    args = ap.parse_args()
    if args.trace:
        tracing.enable_tracing(
            backend="jsonl", output=str(_ROOT / "traces/bakeoff.jsonl")
        )

    resolver = EntityResolver.from_frozen()
    if resolver is None:
        print("FATAL: frozen CIK table not found", file=sys.stderr)
        return 1
    bqs = build_bquestions(resolver)
    sample = bqs if args.n == 0 else bqs[: args.n]

    aspec = llm_io.parse_llm_spec(ANSWER_SPEC)
    jspec = llm_io.parse_llm_spec(JUDGE_SPEC)
    rspec = llm_io.parse_llm_spec(ROUTER_SPEC)
    ac, jc, rc = (llm_io.make_chat_client(s) for s in (aspec, jspec, rspec))

    print("=" * 94)
    print(
        f"Agent-design bake-off (per-step instrumented) — answerer={aspec.model}, "
        f"judge={jspec.model}; {len(sample)} B-questions"
    )
    print("=" * 94)
    agg = {
        a: {"cov": [], "prec": [], "calls": [], "f1": [], "judge": []}
        for a in ARCHETYPES
    }
    for i, bq in enumerate(sample, 1):
        print(f"[{i}/{len(sample)}] {bq.ticker} required={sorted(bq.required)}")
        for name, fn in ARCHETYPES.items():
            ans, fetched, calls = fn(bq, ac, aspec.model, rc, rspec.model)
            cov = len(fetched & bq.required) / len(bq.required)
            prec = (len(fetched & bq.required) / len(fetched)) if fetched else 0.0
            f1 = token_f1(ans, bq.gold)
            js = judge(jc, jspec.model, bq.query, bq.gold, ans)
            for k, v in (
                ("cov", cov),
                ("prec", prec),
                ("calls", calls),
                ("f1", f1),
                ("judge", js),
            ):
                agg[name][k].append(v)

    print(
        f"\n  {'archetype':<14}{'routing_cov':>12}{'routing_prec':>13}{'cost(calls)':>12}"
        f"{'token_f1':>10}{'judge':>8}"
    )
    print("  " + "-" * 70)
    for name in ARCHETYPES:
        a = agg[name]
        m = lambda k: sum(a[k]) / len(a[k])  # noqa: E731
        print(
            f"  {name:<14}{m('cov'):>12.2f}{m('prec'):>13.2f}{m('calls'):>12.1f}"
            f"{m('f1'):>10.3f}{m('judge'):>8.2f}"
        )

    # ---- PER-STEP ATTRIBUTION: where is each archetype good/bad? ----
    print("\n  PER-STEP attribution (step latency + step-correctness, from spans):")
    print(f"  {'archetype.step':<26}{'count':>6}{'mean_ms':>9}{'step_correct':>13}")
    print("  " + "-" * 56)
    by = defaultdict(list)
    for s in STEPS:
        by[(s["archetype"], s["step"])].append(s)
    for (arch, step), rows in sorted(by.items()):
        ms = sum(r["elapsed_ms"] for r in rows) / len(rows)
        corr = [r["step_correct"] for r in rows if "step_correct" in r]
        cr = f"{sum(corr)/len(corr):.2f}" if corr else "-"
        print(f"  {arch+'.'+step:<26}{len(rows):>6}{ms:>9.0f}{cr:>13}")
    print("\n  Reading: the router step's step_correct = gate accuracy; react.decide")
    print(
        "  step_correct<1 = stopped early with a required category unread (the coverage-"
    )
    print(
        "  loss step); plan.subanswer count x mean_ms = where plan_multi's cost goes."
    )
    if args.trace:
        print(
            f"\n  spans -> {_ROOT / 'traces/bakeoff.jsonl'} (query with: seocho traces ...)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
