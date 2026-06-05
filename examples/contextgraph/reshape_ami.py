#!/usr/bin/env python3
"""Reshape the AMI Meeting Corpus manual annotations into the SEOCHO experiment
CSV (same schema as dataset/all_slices.csv / bc3_slices.csv) so the SAME
content-vs-context pipeline can run on a SECOND decision dataset — testing
whether the BC3 finding generalizes (CLAUDE.md §20: a single dataset can't
generalize).

AMI gives, per meeting:
  - words/<mtg>.<spk>.words.xml      → the transcript (CONTENT / references)
  - abstractive/<mtg>.abssumm.xml    → HUMAN abstractive annotation with
      <abstract>  (overall summary)   → GOLD for A2_DECISION_SUMMARY
      <decisions> (what was decided)  → GOLD for A3_DECISIONS
      <actions>   (who will do what)  → GOLD for A4_ACTIONS

Slices (parallel to BC3 E1–E4):
  A1_FACT            query: what was the first thing said, and by whom (vector control)
                     gold : first utterance (single passage)
  A2_DECISION_SUMMARY query: summarize what the meeting discussed and decided
                     gold : human <abstract> sentences
  A3_DECISIONS       query: what decisions were made
                     gold : human <decisions> sentences
  A4_ACTIONS         query: what action items were assigned and to whom
                     gold : human <actions> sentences

references_joined = the transcript grouped into ~contiguous segments (~120 words
each, faithful turns preserved as "Speaker X: ...") joined by ===EVIDENCE_BOUNDARY===
so extraction cost per meeting stays comparable to BC3 (a handful of refs, not
hundreds of word-level turns).

Honest scope: gold is human-annotation-derived and deterministic (annotator
abssumm text) so runs reproduce. Meetings are transcripts, not email — same
decision-making domain, different modality (disclose when comparing to BC3).

Run: python examples/contextgraph/reshape_ami.py
"""
from __future__ import annotations
import csv, glob, re, sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AMI = ROOT / "examples/contextgraph/datasets/raw/ami"
OUT = ROOT / "examples/contextgraph/datasets/ami_slices.csv"
SEP = "===EVIDENCE_BOUNDARY==="
NITE = "{http://nite.sourceforge.net/}"
SEG_WORDS = 120  # group transcript into ~120-word segments (refs)


def _text_sentences(abssumm_path: Path, section: str) -> str:
    """Join the human <section> sentences (abstract/decisions/actions)."""
    try:
        root = ET.parse(abssumm_path).getroot()
    except Exception:
        return ""
    out = []
    for sec in root.iter(section):
        for s in sec.iter("sentence"):
            t = "".join(s.itertext()).strip()
            if t:
                out.append(t)
    return " ".join(out).strip()


def _transcript(meeting: str):
    """Reconstruct time-ordered utterances across speakers from words XML.

    Returns (utterances, first_utterance) where each utterance is "Spk X: text".
    """
    toks = []  # (starttime, speaker, word, is_punc)
    for wf in glob.glob(str(AMI / "words" / f"{meeting}.*.words.xml")):
        spk = Path(wf).name.split(".")[1]  # A/B/C/D
        try:
            root = ET.parse(wf).getroot()
        except Exception:
            continue
        for w in root.iter("w"):
            st = w.get("starttime")
            try:
                st = float(st) if st is not None else None
            except ValueError:
                st = None
            toks.append((st if st is not None else 1e12, spk,
                         (w.text or ""), w.get("punc") == "true"))
    toks.sort(key=lambda t: t[0])
    # group consecutive same-speaker runs into utterances
    utts = []
    cur_spk = None
    cur = []
    for _, spk, word, punc in toks:
        if spk != cur_spk and cur:
            utts.append((cur_spk, _join_words(cur)))
            cur = []
        cur_spk = spk
        cur.append((word, punc))
    if cur:
        utts.append((cur_spk, _join_words(cur)))
    utts = [(s, t) for s, t in utts if t.strip()]
    return utts


def _join_words(words):
    out = ""
    for word, punc in words:
        if punc or not out:
            out += word
        else:
            out += " " + word
    return out.strip()


def _segments(utts, seg_words=SEG_WORDS):
    """Group utterances into ~seg_words-sized contiguous segments (refs)."""
    segs, cur, n = [], [], 0
    for spk, text in utts:
        cur.append(f"Speaker {spk}: {text}")
        n += len(text.split())
        if n >= seg_words:
            segs.append("\n".join(cur)); cur, n = [], 0
    if cur:
        segs.append("\n".join(cur))
    return segs


def main():
    meetings = sorted({Path(p).name.split(".")[0]
                       for p in glob.glob(str(AMI / "abstractive" / "*.abssumm.xml"))})
    rows = []
    skipped = 0
    for mtg in meetings:
        abss = AMI / "abstractive" / f"{mtg}.abssumm.xml"
        summary = _text_sentences(abss, "abstract")
        decisions = _text_sentences(abss, "decisions")
        actions = _text_sentences(abss, "actions")
        utts = _transcript(mtg)
        if not utts or not summary:
            skipped += 1
            continue
        segs = _segments(utts)
        refs_joined = SEP.join(segs)
        first_spk, first_text = utts[0]
        slices = [
            ("A1_FACT", "what was the first thing said in the meeting, and by whom?",
             f"Speaker {first_spk}: {first_text}"),
            ("A2_DECISION_SUMMARY", "summarize what this meeting discussed and decided.", summary),
            ("A3_DECISIONS", "what decisions were made in this meeting?", decisions),
            ("A4_ACTIONS", "what action items were assigned, and to whom?", actions),
        ]
        for slice_, query, gold in slices:
            if not gold.strip():
                continue
            rows.append({
                "slice": slice_, "_id": f"{mtg}#{slice_}", "category": "Decision",
                "type": "", "reasoning": "", "n_refs": len(segs),
                "query_words": len(query.split()), "query": query,
                "answer": gold, "references_joined": refs_joined,
            })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["slice", "_id", "category", "type", "reasoning",
                                          "n_refs", "query_words", "query", "answer",
                                          "references_joined"])
        w.writeheader()
        w.writerows(rows)
    by_slice = defaultdict(int)
    for r in rows:
        by_slice[r["slice"]] += 1
    print(f"wrote {len(rows)} rows -> {OUT}")
    print(f"meetings used: {len({r['_id'].split('#')[0] for r in rows})}  (skipped {skipped})")
    for s in sorted(by_slice):
        print(f"  {s:<22} {by_slice[s]}")


if __name__ == "__main__":
    main()
