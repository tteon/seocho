#!/usr/bin/env python3
"""Gold-tuple builder for HOLDS_POSITION accuracy — M2.7, NO human review (B2).

Drafts gold (person, polarity, topic) tuples for E4_POSITIONS from the GOLD
ANSWER + references_joined (source text) — never from the graph. polarity is the
enumerable FOR|AGAINST|NEUTRAL that the position-arm aggregation reads.

§20 DISCLOSURE: M2.7 MODEL-DRAFTED, unvalidated (B2). Proxy gold (stronger than
the unstable answer-judge — kappa 0.2-0.5 — but inherits M2.7 bias). Used to score
the position-arm extraction's tuple-F1, bypassing the arbiter.

Run: python examples/contextgraph/build_position_gold_m27.py
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
OUT = ROOT / "outputs/evaluation/contextgraph/position_gold_m27.json"

SYS = (
    "You extract the GOLD positions a correct answer must contain for a "
    "'what positions/opinions did participants express on which topics' question. "
    "Given the QUESTION, the human GOLD ANSWER, and the source EVIDENCE (email "
    "text), list each distinct position actually expressed. Extract ONLY from the "
    "gold answer and evidence — never invent. Reason privately, output STRICT JSON.\n\n"
    "Each fact = a person taking a position on a TOPIC:\n"
    '  {"person":"<full name>","polarity":"FOR|AGAINST|NEUTRAL","topic":"<3-8 word topic gist>",'
    '"quote":"<short verbatim from evidence>"}\n'
    "FOR = supports/agrees/prefers; AGAINST = objects/opposes/raises a blocking "
    "concern; NEUTRAL = a view with no clear direction. Merge duplicates (one "
    "person+topic+direction = one fact). A pure question/request with no expressed "
    "view is NOT a position. Output: {\"facts\":[ ... ]}"
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
    print("=" * 70)
    print("DISCLOSURE: position gold tuples are M2.7 MODEL-DRAFTED, unvalidated (B2).")
    print("=" * 70)
    rows = [r for r in csv.DictReader(open(DATA)) if r["slice"] == "E4_POSITIONS"]
    llm = create_llm_backend(provider="mara", model="MiniMax-M2.7")
    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    for r in rows:
        key = r["_id"]
        if key in out:
            continue
        refs = str(r["references_joined"]).replace("===EVIDENCE_BOUNDARY===", "\n---\n")
        user = (f"QUESTION:\n{r['query']}\n\nGOLD ANSWER:\n{r['answer']}\n\n"
                f"EVIDENCE:\n{refs[:6000]}\n\nExtract the gold positions as JSON.")
        try:
            facts = _parse(getattr(llm.complete(system=SYS, user=user, temperature=0.0), "text", ""))
        except Exception as e:
            facts = None
            print(f"  {key}: ERROR {type(e).__name__}: {str(e)[:60]}")
        out[key] = {"_id": key, "query": r["query"], "facts": facts, "source": "m27-drafted-unvalidated"}
        OUT.write_text(json.dumps(out, indent=2))
        if facts is not None:
            print(f"  {key}: {len(facts)} positions")
    ok = [v for v in out.values() if v.get("facts") is not None]
    tot = sum(len(v["facts"]) for v in ok)
    print(f"\ndrafted {len(ok)}/{len(rows)} cases, {tot} gold positions -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
