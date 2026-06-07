#!/usr/bin/env python3
"""5-archetype agent-design bake-off over the instance/schema/data layering (seocho-q8x).

Compares orchestration patterns by HOW they navigate the schema layer (which
FinDER categories to fetch) for cross-category B-questions, gated by the
intent router (seocho-tgi). PRIMARY metric is the deterministic layer-routing
correctness (coverage of gold-required categories, routing precision/over-fetch,
cost = #LLM calls); token_f1 + a CROSS-MODEL judge (gpt-oss-120b) are secondary
(the LLM judge is noisy — rho=0.183, PR #206).

Archetypes (the design spectrum; not faithful clones of 7 frameworks):
  single_shot     no retrieval (floor)
  intent_gated    router -> fetch gated categories -> answer (1 pass; GraphCoT)
  react           reason+act loop: the LLM picks the next section to read (tool),
                  observes, repeats, then answers (agent decides what to look at)
  reflexion       intent_gated answer -> self-critique -> re-fetch missing -> re-answer
  plan_multi      planner splits into per-category sub-questions -> per-category
                  answers -> composer merges (MetaGPT/CAMEL-lite multi-agent)

Tenant/company isolation (instance layer) is CIK-scoped by construction here and
was proven live in finder_agent_isolation_cooperation (#196); this harness
focuses on the schema-navigation differences. MARA-first.

Run: PYTHONPATH=src:scripts/benchmarks python3 scripts/benchmarks/finder_agent_designs.py --n 4
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
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
from seocho.semantic_layer.identity import EntityResolver  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

JUDGE_SPEC = "mara/gpt-oss-120b"      # cross-model judge (different from answerer)
ROUTER_SPEC = "mara/gpt-oss-120b"     # LLM router (better cross-detection, #207)


@dataclass
class Trace:
    fetched: Set[str] = field(default_factory=set)
    llm_calls: int = 0


def _fetch(bq, cat: str) -> str:
    """Schema-layer fetch: a category's evidence for THIS company (CIK-scoped)."""
    return "\n\n".join(bq.ev_by_cat.get(cat, []))


def _ctx(bq, cats) -> str:
    blocks = [f"[{c}]\n{_fetch(bq, c)}" for c in sorted(cats) if _fetch(bq, c)]
    return "\n\n".join(blocks)[:CONTEXT_BUDGET]


# --------------------------------------------------------------------------
# Archetypes — each returns (answer_text, Trace)
# --------------------------------------------------------------------------
def a_single_shot(bq, ac, am, rc, rm) -> tuple:
    t = Trace()
    t.llm_calls += 1
    return answer(ac, am, bq.query, ""), t


def a_intent_gated(bq, ac, am, rc, rm) -> tuple:
    t = Trace()
    cats = set(_llm_route(bq.query, rc, rm) or route(bq.query).required_categories)
    t.llm_calls += 1                       # router call
    t.fetched = cats
    t.llm_calls += 1                       # answer call
    return answer(ac, am, bq.query, _ctx(bq, cats)), t


def a_react(bq, ac, am, rc, rm, max_steps: int = 3) -> tuple:
    t = Trace()
    seen: Dict[str, str] = {}
    for _ in range(max_steps):
        opts = ", ".join(c for c in CATEGORIES if c not in seen) or "(none)"
        sysmsg = ("You are answering a 10-K question. Decide the NEXT action. "
                  f"Reply with ONE token: a section to read [{opts}] or ANSWER if "
                  "you have enough. Output only the token.")
        user = (f"Question: {bq.query}\nAlready read: {list(seen)}\n"
                f"Evidence so far:\n{chr(10).join(seen.values())[:1200]}")
        r = ac.chat.completions.create(model=am, temperature=0, max_tokens=300,
                                       messages=[{"role": "system", "content": sysmsg},
                                                 {"role": "user", "content": user}])
        t.llm_calls += 1
        choice = (r.choices[0].message.content or "").upper()
        picked = next((c for c in CATEGORIES if c.upper() in choice and c not in seen), None)
        if picked is None:                 # ANSWER or nothing new
            break
        seen[picked] = f"[{picked}]\n{_fetch(bq, picked)}"
    t.fetched = set(seen)
    t.llm_calls += 1
    return answer(ac, am, bq.query, "\n\n".join(seen.values())[:CONTEXT_BUDGET]), t


def a_reflexion(bq, ac, am, rc, rm) -> tuple:
    t = Trace()
    cats = set(_llm_route(bq.query, rc, rm) or route(bq.query).required_categories)
    t.llm_calls += 1
    ans = answer(ac, am, bq.query, _ctx(bq, cats))
    t.llm_calls += 1
    # self-critique: which sections are still missing to fully answer?
    crit_sys = ("Critique whether the answer fully addresses EVERY part of the "
                "question. Reply with a comma list of any additional sections "
                f"needed from [{', '.join(CATEGORIES)}], or NONE. Output only that.")
    r = ac.chat.completions.create(model=am, temperature=0, max_tokens=400,
                                   messages=[{"role": "system", "content": crit_sys},
                                             {"role": "user", "content": f"Q: {bq.query}\nA: {ans}"}])
    t.llm_calls += 1
    extra = {c for c in CATEGORIES if c.lower() in (r.choices[0].message.content or "").lower()}
    if extra - cats:
        cats |= extra
        ans = answer(ac, am, bq.query, _ctx(bq, cats))   # re-answer with more
        t.llm_calls += 1
    t.fetched = cats
    return ans, t


def a_plan_multi(bq, ac, am, rc, rm) -> tuple:
    t = Trace()
    cats = set(_llm_route(bq.query, rc, rm) or route(bq.query).required_categories)
    t.llm_calls += 1
    t.fetched = cats
    sub = []
    for c in sorted(cats):                 # a specialist per required category
        sub.append(f"[{c}] " + answer(ac, am, bq.query, _ctx(bq, {c})))
        t.llm_calls += 1
    compose = ("Compose a single answer from these per-section findings. "
               "Be specific; keep the numbers.")
    r = ac.chat.completions.create(model=am, temperature=0, max_tokens=900,
                                   messages=[{"role": "system", "content": compose},
                                             {"role": "user", "content": f"Q: {bq.query}\n\n" + "\n\n".join(sub)}])
    t.llm_calls += 1
    return (r.choices[0].message.content or "").strip(), t


ARCHETYPES = {
    "single_shot": a_single_shot,
    "intent_gated": a_intent_gated,
    "react": a_react,
    "reflexion": a_reflexion,
    "plan_multi": a_plan_multi,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4, help="number of B-questions (0 = all 17)")
    args = ap.parse_args()

    resolver = EntityResolver.from_frozen()
    if resolver is None:
        print("FATAL: frozen CIK table not found", file=sys.stderr)
        return 1
    bqs = build_bquestions(resolver)
    sample = bqs if args.n == 0 else bqs[:args.n]

    aspec = llm_io.parse_llm_spec(ANSWER_SPEC)
    jspec = llm_io.parse_llm_spec(JUDGE_SPEC)
    rspec = llm_io.parse_llm_spec(ROUTER_SPEC)
    ac = llm_io.make_chat_client(aspec)
    jc = llm_io.make_chat_client(jspec)
    rc = llm_io.make_chat_client(rspec)

    print("=" * 92)
    print(f"Agent-design bake-off — answerer={aspec.model}, judge={jspec.model}, "
          f"router={rspec.model}; {len(sample)} cross-category B-questions")
    print("=" * 92)
    agg: Dict[str, dict] = {a: {"cov": [], "prec": [], "calls": [], "f1": [], "judge": []}
                            for a in ARCHETYPES}
    for i, bq in enumerate(sample, 1):
        req = bq.required
        print(f"\n[{i}/{len(sample)}] {bq.ticker} required={sorted(req)}")
        for name, fn in ARCHETYPES.items():
            ans, t = fn(bq, ac, aspec.model, rc, rspec.model)
            cov = len(t.fetched & req) / len(req)
            prec = (len(t.fetched & req) / len(t.fetched)) if t.fetched else 0.0
            f1 = token_f1(ans, bq.gold)
            js = judge(jc, jspec.model, bq.query, bq.gold, ans)
            for k, v in (("cov", cov), ("prec", prec), ("calls", t.llm_calls),
                         ("f1", f1), ("judge", js)):
                agg[name][k].append(v)
            print(f"   {name:<13} cov={cov:.2f} prec={prec:.2f} calls={t.llm_calls} "
                  f"f1={f1:.3f} judge={js:.2f}")
    print("\n" + "=" * 92)
    print(f"  {'archetype':<14}{'routing_cov':>12}{'routing_prec':>13}{'cost(calls)':>12}"
          f"{'token_f1':>10}{'judge':>8}")
    print("  " + "-" * 70)
    for name in ARCHETYPES:
        a = agg[name]
        m = lambda k: sum(a[k]) / len(a[k])  # noqa: E731
        print(f"  {name:<14}{m('cov'):>12.2f}{m('prec'):>13.2f}{m('calls'):>12.1f}"
              f"{m('f1'):>10.3f}{m('judge'):>8.2f}")
    print("\n  PRIMARY = deterministic routing (cov/prec) + cost; answer quality secondary")
    print("  (LLM judge noisy). Tradeoff: more orchestration (react/reflexion/plan) costs")
    print("  more LLM calls — is the coverage/quality gain worth it vs intent_gated (1 pass)?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
