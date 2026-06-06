#!/usr/bin/env python3
"""Experiment 0 — per-slice failure-mode decomposition (DETERMINISTIC, $0, NO LLM).

Panel-corrected scope (Harvard SWE + Meta-scale architect cross-review, 2026-06-06):
the professor's 4-way taxonomy (format / recall-miss / comprehension-mangle /
hallucination) is NOT cleanly recoverable on current artifacts — recall-miss vs
comprehension-mangle is circular without a frozen gold-TUPLE set, and would
manufacture a measured distinction out of judge noise on the exact seam being
studied (§20.1/§20.8 violation). So we report ONLY what is deterministic and $0:

  (1) FORMAT-LOSS  — gold content tokens that ARE in the workspace's raw Chunk
      text but ARE DROPPED by the graph serializer (`_graph_context`, which dumps
      typed nodes + rels but never the Chunk `text`). This is recoverable signal
      the SERIALIZER deletes — fixable without touching extraction. (GRL memory:
      the graph-lane loss is the serializer dropping raw Chunk, not recall.)
  (2) NOT-RECOVERABLE (cause TBD) — gold tokens absent even from Chunk text:
      upstream extraction/chunking loss. Honest single bucket; we DO NOT split it
      into recall-vs-comprehension until a gold-tuple set exists.
  (3) ADMISSION + correct|admitted — from the C judge file (already materialized):
      verifiable flag = admitted; judge_verdict = correct. The serving gate.

Serving-weighted view (Meta architect): a comprehension-mangle that is SERVED
LLM-free (admitted AND wrong) is categorically worse than a miss (admitted=False,
graceful LLM fallback). We surface admitted-AND-wrong as the silent-wrong rate —
the mode that gates whether LLM-free serving is safe to turn ON at all.

Tokenization: lowercase \\w+, drop English stopwords + pure-punctuation, keep
numerics. Content-token overlap only (not exact match) — canonicalization-tolerant.

Run: python examples/contextgraph/failure_modes.py
"""
from __future__ import annotations
import csv, json, logging, os, re, sys
from pathlib import Path
from collections import defaultdict

logging.getLogger("neo4j").setLevel(logging.ERROR)  # silence harmless a.uri-missing notifications

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples" / "contextgraph"))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
from seocho.store.graph import Neo4jGraphStore
from scripts.benchmarks.finder_4arm_sample import _graph_context

DATA = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
DB = "cgbc3minimaxm25"
WS_RUN = "e1-bc3-a1"
C_JUDGED = ROOT / "outputs/evaluation/contextgraph/e1-bc3-detgraph_judged.json"
N_THREADS = 15

_STOP = set("""a an the of to in on at by for and or but if then else with without within into onto from as is
are was were be been being do does did have has had this that these those it its their his her our your my we
you they he she i not no nor so than too very can will would should could may might must shall about above
below over under again further once here there all any both each few more most other some such only own same
who whom which what when where why how do done make made get got""".split())


def _toks(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", str(s).lower())
            if t not in _STOP and (len(t) > 2 or t.isdigit())}


def main():
    rows = list(csv.DictReader(open(DATA)))
    by_thread = defaultdict(list)
    for c in rows:
        by_thread[str(c["_id"]).split("#")[0]].append(c)
    tids = list(by_thread)[:N_THREADS]

    # C judge: admission (verifiable) + correctness per case base-id
    cj = json.load(open(C_JUDGED))
    admitted, correct = {}, {}
    for r in cj["results"]:
        base = str(r["_id"]).split("|")[0]
        admitted[base] = bool(r.get("verifiable"))
        correct[base] = (r.get("judge_verdict") == "correct") or (r.get("judge_score", 0) >= 1.0)

    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    per = defaultdict(lambda: {"n": 0, "recov": 0.0, "seen": 0.0, "adm": 0,
                               "adm_correct": 0, "adm_wrong": 0, "judged": 0})
    try:
        for tid in tids:
            w = f"{WS_RUN}-decision-{tid}"
            # raw Chunk text (the ceiling — what the documents actually hold)
            chunks = gs.query(
                "MATCH (n:Chunk {_workspace_id:$w}) RETURN coalesce(n.text,'') AS t",
                params={"w": w}, database=DB) or []
            chunk_tok = set()
            for ch in chunks:
                chunk_tok |= _toks(ch["t"])
            # what the serializer actually exposes to the answerer
            ser_tok = _toks(_graph_context(gs, w, DB))
            for c in by_thread[tid]:
                sl = c["slice"]
                gold = _toks(c["answer"])
                if not gold:
                    continue
                recov = len(gold & chunk_tok) / len(gold)        # ceiling: in documents
                seen = len(gold & ser_tok) / len(gold)            # what serializer shows
                d = per[sl]
                d["n"] += 1
                d["recov"] += recov
                d["seen"] += seen
                base = c["_id"]
                if base in admitted:
                    d["judged"] += 1
                    if admitted[base]:
                        d["adm"] += 1
                        if correct.get(base):
                            d["adm_correct"] += 1
                        else:
                            d["adm_wrong"] += 1
    finally:
        gs.close()

    print("== Experiment 0: failure-mode decomposition (BC3, 15 threads, $0 no-LLM) ==")
    print("   deterministic-only; recall-vs-comprehension NOT split (no gold-tuple set yet)\n")
    print(f"  {'slice':<20}{'recoverable':>12}{'serializer':>11}{'FORMAT-LOSS':>13}{'not-recov':>11}"
          f"{'admit':>8}{'corr|adm':>9}{'SILENT-WRONG':>14}{'n':>4}")
    tot = defaultdict(float)
    for sl in sorted(per):
        d = per[sl]; n = d["n"] or 1; j = d["judged"] or 1
        recov = d["recov"] / n          # gold tokens present in raw Chunk text
        seen = d["seen"] / n            # gold tokens present in serializer output
        floss = max(0.0, recov - seen)  # in-chunk but serializer dropped (FIXABLE)
        notrec = 1.0 - recov           # not even in chunk (cause TBD, upstream)
        adm = d["adm"] / j
        cga = (d["adm_correct"] / d["adm"]) if d["adm"] else 0.0
        sw = d["adm_wrong"] / j         # admitted AND wrong = served silent-wrong
        print(f"  {sl:<20}{recov:>11.0%}{seen:>11.0%}{floss:>12.0%}{notrec:>11.0%}"
              f"{adm:>8.0%}{cga:>9.2f}{sw:>13.0%}{d['n']:>4}")
        for k, val in (("recov", recov), ("seen", seen), ("floss", floss),
                       ("notrec", notrec), ("sw", sw)):
            tot[k] += val
    ns = len(per) or 1
    print("\n  -- interpretation (serving-weighted; silent-wrong >> miss) --")
    print(f"  mean FORMAT-LOSS (serializer-dropped, FIXABLE): {tot['floss']/ns:.0%}")
    print(f"  mean NOT-RECOVERABLE (upstream, cause TBD):     {tot['notrec']/ns:.0%}")
    print(f"  mean SILENT-WRONG (admitted & wrong, served):   {tot['sw']/ns:.0%}")
    print("\n  read: high FORMAT-LOSS + low NOT-RECOVERABLE => serializer is the binding")
    print("        constraint (the signal is IN the graph's chunks, the serializer drops it).")
    print("        high NOT-RECOVERABLE => upstream extraction/chunking (gold-tuple set needed).")

    out = ROOT / "outputs/evaluation/contextgraph/failure_modes_bc3.json"
    rec = {"dataset": "bc3", "n_threads": N_THREADS, "db": DB, "ws_run": WS_RUN,
           "note": "deterministic-only; recall-vs-comprehension NOT split (no gold-tuple set)",
           "per_slice": {}}
    for sl in sorted(per):
        d = per[sl]; n = d["n"] or 1; j = d["judged"] or 1
        recov = d["recov"] / n; seen = d["seen"] / n
        rec["per_slice"][sl] = {
            "n": d["n"], "recoverable": round(recov, 3), "serializer_seen": round(seen, 3),
            "format_loss": round(max(0.0, recov - seen), 3), "not_recoverable": round(1 - recov, 3),
            "admit": round(d["adm"] / j, 3),
            "correct_given_admit": round((d["adm_correct"] / d["adm"]) if d["adm"] else 0.0, 3),
            "silent_wrong": round(d["adm_wrong"] / j, 3)}
    out.write_text(json.dumps(rec, indent=2))
    print(f"\n  artifact -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
