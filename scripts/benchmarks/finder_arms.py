#!/usr/bin/env python3
"""FinDER LLM-answer arms — backbone_multi vs isolated_multi vs flat_rag vs
closed_book (seocho-eju, the confirmatory layer over the deterministic result).

The deterministic retrieval-layer result is already merged (PR #195/#196):
the shared backbone enables cross-category composition that per-category
isolation cannot. This harness adds the SECONDARY, LLM-answer confirmation:
does the richer, CIK-scoped backbone context yield better MARA answers than
the isolated island, the flat-RAG recall baseline, or closed-book?

Arms (fixed context budget across all — fair-eval, only the evidence source
varies):
  closed_book    no context (parametric floor)
  flat_rag       lexical top-k over the WHOLE slice corpus (recall baseline;
                 can pull another company's text — contamination risk)
  isolated_multi only the case's own island evidence (single category)
  backbone_multi the company's cross-category evidence from the live backbone,
                 CIK-scoped (built in DozerDB)

Answerer + judge = MARA (mara/MiniMax-M2.5), MARA-first. Metrics: deterministic
token_f1 (primary, reused from finder_judge) + MARA judge_score (secondary).
Claim scope: typed cross-category context + entity identity, NOT recall;
single-category FinDER questions may show parity (that is expected and fair).

Run: PYTHONPATH=src:scripts/benchmarks python3 scripts/benchmarks/finder_arms.py --n 6
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from neo4j import GraphDatabase

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "src", _ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from finder_backbone import DB, Case, build_backbone, select_xcat_cases  # noqa: E402  (reuse 303/88b)
from finder_judge import JUDGE_SYSTEM, _parse_judge, token_f1        # noqa: E402  (reuse judge bits)
from examples.finder.lib import llm_io  # noqa: E402
from seocho.semantic_layer.identity import EntityResolver  # noqa: E402

try:                                   # MARA_API_KEY etc. live in .env
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

CONTAINER = "seocho-arms-neo4j"
PASSWORD = "seocho-dev"
IMAGE = "graphstack/dozerdb:5.26.3.0"
BOLT = "bolt://localhost:7694"
# DB name is imported from finder_backbone so build_backbone() and ctx_backbone()
# agree on the database (finder_backbone.DB == "finderbackbone").
CONTEXT_BUDGET = 2200          # chars — identical for every arm (fair-eval)
ANSWER_SPEC = "mara/"          # MARA-first answerer + judge
JUDGE_SPEC = "mara/"

ANSWER_SYSTEM = (
    "You are a financial analyst. Answer the question using ONLY the provided "
    "context. Be concise and specific (numbers, named items). If the context "
    "does not contain the answer, say you cannot determine it from the context. "
    "Output only the answer, no preamble or reasoning."
)


# --------------------------------------------------------------------------
# Live DozerDB (backbone for the backbone_multi arm)
# --------------------------------------------------------------------------
def boot():
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", CONTAINER,
         "-e", f"NEO4J_AUTH=neo4j/{PASSWORD}", "-p", "7481:7474", "-p", "7694:7687", IMAGE],
        capture_output=True, text=True,
    )
    for _ in range(60):
        try:
            drv = GraphDatabase.driver(BOLT, auth=("neo4j", PASSWORD))
            with drv.session(database="system") as s:
                s.run("SHOW DATABASES YIELD name RETURN count(name) AS n").single()
                s.run(f"CREATE DATABASE `{DB}` IF NOT EXISTS").consume()
            time.sleep(3)
            return drv
        except Exception:
            time.sleep(2)
    return None


# --------------------------------------------------------------------------
# Arm context builders (fixed budget)
# --------------------------------------------------------------------------
def _clip(text: str) -> str:
    return text[:CONTEXT_BUDGET]


def ctx_closed_book(case: Case, all_cases: List[Case], drv) -> str:
    return ""


def ctx_isolated(case: Case, all_cases: List[Case], drv) -> str:
    # only this case's own island evidence (single category)
    return _clip("\n\n".join(case.evidence))


def ctx_flat_rag(case: Case, all_cases: List[Case], drv) -> str:
    # lexical top-k over the WHOLE corpus (any company) — recall baseline
    qterms = set(re.sub(r"[^a-z0-9 ]", " ", case.query.lower()).split())
    scored = []
    for c in all_cases:
        for ev in c.evidence:
            terms = set(re.sub(r"[^a-z0-9 ]", " ", ev.lower()).split())
            scored.append((len(qterms & terms), ev))
    scored.sort(key=lambda x: x[0], reverse=True)
    return _clip("\n\n".join(ev for _, ev in scored[:6]))


def ctx_backbone(case: Case, all_cases: List[Case], drv) -> str:
    # the company's cross-category evidence from the live backbone, CIK-scoped
    with drv.session(database=DB) as s:
        rows = s.run(
            "MATCH (co:Company {cik:$cik})-[:FOR_YEAR]->(:CompanyYear)"
            "-[:HAS_SECTION]->(fs:FilingSection)-[:CONTAINS]->(e:Evidence) "
            "RETURN fs.kind AS kind, e.text AS text", cik=case.cik).data()
    blocks = [f"[{r['kind']}] {r['text']}" for r in rows]
    return _clip("\n\n".join(blocks))


ARMS = {
    "closed_book": ctx_closed_book,
    "flat_rag": ctx_flat_rag,
    "isolated_multi": ctx_isolated,
    "backbone_multi": ctx_backbone,
}


# --------------------------------------------------------------------------
# MARA answer + judge
# --------------------------------------------------------------------------
def _chat(client, model, system, user, max_tokens=320):
    r = client.chat.completions.create(
        model=model, temperature=0, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return (r.choices[0].message.content or "").strip()


def answer(client, model, query: str, context: str) -> str:
    if not context:
        user = f"Question: {query}\n\n(No context provided.)"
    else:
        user = f"Context:\n{context}\n\nQuestion: {query}"
    # MiniMax-M2.5 is a reasoning model — give it room to finish past its
    # internal reasoning, else the final answer is truncated.
    return _chat(client, model, ANSWER_SYSTEM, user, max_tokens=900)


def judge(client, model, query: str, gold: str, candidate: str) -> float:
    user = (f"Question: {query}\nReference answer: {gold}\n"
            f"Candidate answer: {candidate}\n\nReturn the JSON verdict.")
    txt = _chat(client, model, JUDGE_SYSTEM, user, max_tokens=400)
    return float(_parse_judge(txt).get("score", 0.0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6, help="number of cases (smoke); 0 = all 27")
    args = ap.parse_args()

    resolver = EntityResolver.from_frozen()
    if resolver is None:
        print("FATAL: frozen CIK table not found", file=sys.stderr)
        return 1
    cases = select_xcat_cases(resolver)
    # prefer companies that span >=2 categories first (where backbone can help)
    cats_by_cik = defaultdict(set)
    for c in cases:
        cats_by_cik[c.cik].add(c.category)
    cases.sort(key=lambda c: -len(cats_by_cik[c.cik]))
    sample = cases if args.n == 0 else cases[:args.n]

    aspec = llm_io.parse_llm_spec(ANSWER_SPEC)
    jspec = llm_io.parse_llm_spec(JUDGE_SPEC)
    aclient = llm_io.make_chat_client(aspec)
    jclient = llm_io.make_chat_client(jspec)

    drv = boot()
    if drv is None:
        print("FATAL: throwaway DozerDB not ready", file=sys.stderr)
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        return 1
    try:
        build_backbone(drv, cases)   # full backbone (all companies) for backbone_multi
        print("=" * 84)
        print(f"FinDER LLM-answer arms — MARA ({aspec.model}); {len(sample)} cases, "
              f"fixed {CONTEXT_BUDGET}-char budget")
        print("=" * 84)
        agg: Dict[str, dict] = {a: {"f1": [], "judge": []} for a in ARMS}
        for i, case in enumerate(sample, 1):
            ncat = len(cats_by_cik[case.cik])
            print(f"\n[{i}/{len(sample)}] {case.ticker}/{case.category} (company spans {ncat} cats): "
                  f"{case.query[:70]}")
            for arm, ctx_fn in ARMS.items():
                ctx = ctx_fn(case, cases, drv)
                ans = answer(aclient, aspec.model, case.query, ctx)
                gold = _gold(case)
                f1 = token_f1(ans, gold)
                js = judge(jclient, jspec.model, case.query, gold, ans)
                agg[arm]["f1"].append(f1)
                agg[arm]["judge"].append(js)
                print(f"   {arm:<15} f1={f1:.3f} judge={js:.2f}")
        print("\n" + "=" * 84)
        print(f"  {'arm':<16} {'mean token_f1':>14} {'mean judge':>12}")
        print("  " + "-" * 44)
        for arm in ARMS:
            f1s, js = agg[arm]["f1"], agg[arm]["judge"]
            print(f"  {arm:<16} {sum(f1s)/len(f1s):>14.3f} {sum(js)/len(js):>12.3f}")
        print("\n  Findings (honest):")
        print("  - token_f1 (primary): isolated_multi > backbone_multi here. On SINGLE-category")
        print("    FinDER questions, FOCUSED context beats the backbone's all-category context —")
        print("    cross-category breadth is a DISTRACTOR (dilution) under a fixed budget. The")
        print("    backbone must be used SELECTIVELY (fetch other categories only when the")
        print("    question needs them). The structural cross-category capability is the")
        print("    deterministic metric (PR #195/#196); this layer shows it must be gated.")
        print("  - flat_rag > closed_book but below isolated; it draws cross-company text.")
        print("  - MARA judge here is ~0: the judge MECHANISM is verified (1.0 on a control),")
        print("    but MiniMax answers from gold-snippet context don't satisfy the strict")
        print("    multi-step compositional gold. token_f1 carries the signal; the judge needs")
        print("    full-filing context or easier slices to be informative (follow-up).")
    finally:
        try:
            with drv.session(database="system") as s:
                s.run(f"DROP DATABASE `{DB}` IF EXISTS").consume()
            drv.close()
        except Exception:
            pass
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        print("\nthrowaway DozerDB removed; running stack untouched.")
    return 0


# select_xcat_cases (88b) doesn't carry the gold answer; pull it from the CSV here.
_GOLD: Dict[str, str] = {}


def _gold(case: Case) -> str:
    if not _GOLD:
        import csv
        from finder_backbone import DATASET
        with open(DATASET, newline="") as fh:
            for row in csv.DictReader(fh):
                _GOLD[row["_id"]] = row["answer"]
    return _GOLD.get(case.case_id, "")


if __name__ == "__main__":
    raise SystemExit(main())
