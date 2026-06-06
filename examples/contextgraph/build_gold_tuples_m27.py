#!/usr/bin/env python3
"""Gold-tuple builder (B2) — MiniMax-M2.7 drafts gold extraction tuples, NO human review.

The arbiter (LLM answer-judge) is unstable across model AND process (kappa 0.2-0.5;
project-arbiter-calibration). So fine-grained prompt optimization must be measured
by a $0 deterministic STAGE-LOCAL extraction metric that BYPASSES the judge:
tuple precision/recall/F1 of the extracted graph vs a gold-tuple set.

This drafts that gold-tuple set for E3_PROPOSALS / E4_POSITIONS from the GOLD
ANSWER + references_joined (the actual email text = source of truth) — NEVER from
the extracted graph (the graph is the thing under test; reading it would be
circular). Gold tuples:
  E3_PROPOSALS  -> {proposer, proposal_gist}      (who proposed what)
  E4_POSITIONS  -> {person, direction(FOR|AGAINST), proposal_gist}  (who took which stance)

§20 DISCLOSURE (B2, user choice): these gold tuples are MODEL-DRAFTED by M2.7 and
NOT human-validated. They are a proxy gold — a stronger basis than the noisy
answer-judge, but they inherit M2.7's biases and cannot be called human ground
truth. Recorded in the artifact + printed at runtime.

Run: python examples/contextgraph/build_gold_tuples_m27.py
"""
from __future__ import annotations
import csv, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
from seocho.store.llm import create_llm_backend

DATA = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
OUT = ROOT / "outputs/evaluation/contextgraph/gold_tuples_m27.json"
SLICES = {"E3_PROPOSALS", "E4_POSITIONS"}

SYS = (
    "You extract the GOLD FACTS that a correct answer to a decision-email question "
    "must contain. You are given the QUESTION, the human GOLD ANSWER, and the source "
    "EVIDENCE (the actual email text). Extract atomic facts ONLY from the gold answer "
    "and the evidence — never invent. Use the evidence to ground and to get canonical "
    "full names. Reason privately, then output STRICT JSON only.\n\n"
    "For a PROPOSALS question, each fact = a distinct proposal actually made:\n"
    '  {"proposer":"<full name>","proposal_gist":"<3-8 word canonical gist of the option>",'
    '"quote":"<short verbatim from evidence>"}\n'
    "For a POSITIONS question, each fact = a person taking a stance on a specific proposal:\n"
    '  {"person":"<full name>","direction":"FOR|AGAINST","proposal_gist":"<3-8 word gist>",'
    '"quote":"<short verbatim from evidence>"}\n\n'
    "Merge duplicates (one proposal = one fact even if restated). Only include a "
    "stance that is a clear support/opposition (not a question/hedge). Output:\n"
    '{"facts":[ ... ]}'
)


def _parse(text):
    m = re.search(r"\{.*\}", str(text), re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0)).get("facts", [])
    except Exception:
        return None


def main():
    print("=" * 72)
    print("DISCLOSURE: gold tuples are M2.7 MODEL-DRAFTED, NOT human-validated (B2).")
    print("Proxy gold — stronger than the noisy answer-judge, but inherits M2.7 bias.")
    print("Drawn from GOLD ANSWER + references (source text), never from the graph.")
    print("=" * 72)
    rows = [r for r in csv.DictReader(open(DATA)) if r["slice"] in SLICES]
    llm = create_llm_backend(provider="mara", model="MiniMax-M2.7")
    out = {}
    if OUT.exists():
        out = json.loads(OUT.read_text())
    for r in rows:
        cid = r["_id"]
        key = f"{r['slice']}|{cid}"
        if key in out:
            continue
        refs = str(r["references_joined"]).replace("===EVIDENCE_BOUNDARY===", "\n---\n")
        user = (f"QUESTION:\n{r['query']}\n\nGOLD ANSWER:\n{r['answer']}\n\n"
                f"EVIDENCE (source email text):\n{refs[:6000]}\n\nExtract the gold facts as JSON.")
        try:
            resp = llm.complete(system=SYS, user=user, temperature=0.0)
            facts = _parse(getattr(resp, "text", resp))
        except Exception as e:
            facts = None
            print(f"  {key}: ERROR {type(e).__name__}: {str(e)[:70]}")
        out[key] = {"slice": r["slice"], "_id": cid, "query": r["query"],
                    "facts": facts, "source": "m27-drafted-unvalidated"}
        OUT.write_text(json.dumps(out, indent=2))  # incremental (§ cost)
        if facts is not None:
            print(f"  {key}: {len(facts)} facts")
    n_ok = sum(1 for v in out.values() if v.get("facts") is not None)
    tot = sum(len(v["facts"]) for v in out.values() if v.get("facts"))
    print(f"\ndrafted {n_ok}/{len(rows)} cases, {tot} gold tuples -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
