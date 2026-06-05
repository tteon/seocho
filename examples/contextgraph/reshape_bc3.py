#!/usr/bin/env python3
"""Reshape the BC3 corpus into the SEOCHO experiment CSV (same schema as
dataset/all_slices.csv) so the FinDER harness can run content-vs-context on
decision-making email threads.

BC3 gives, per thread: messages (corpus.xml) + 3 human annotations
(annotation.xml) = extractive summary + sentence speech-act labels
(prop/req/cmt/subj/meet/meta). We turn those human labels into GOLD:

  E2_DECISION_SUMMARY  query: summarize the discussion/decision
                       gold : a human extractive summary (annotator-0 text)
  E3_PROPOSALS         query: what was proposed, by whom
                       gold : sentences labelled `prop` by >=2 annotators (consensus)
  E4_POSITIONS         query: who supported / objected, and why
                       gold : `subj` consensus sentences (positions/opinions)
  E1_FACT              query: who initiated the thread and when  (vector control)
                       gold : first message sender + date

references_joined = each message's text, joined by ===EVIDENCE_BOUNDARY===.

Output: examples/contextgraph/datasets/bc3_slices.csv
Honest scope: gold is human-annotation-derived and deterministic (annotator-0
summary; >=2-annotator consensus for labels) so runs reproduce. n per slice is
reported; threads lacking a label type are skipped for that slice (not faked).
"""
from __future__ import annotations
import csv
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "datasets" / "raw"
OUT = ROOT / "datasets" / "bc3_slices.csv"
SEP = "===EVIDENCE_BOUNDARY==="
COLS = ["slice", "_id", "category", "type", "reasoning", "n_refs",
        "query_words", "query", "answer", "references_joined"]


def _clean(s: str) -> str:
    return " ".join((s or "").split()).strip()


def parse_corpus():
    root = ET.parse(RAW / "bc3_corpus.xml").getroot()
    threads = {}
    for t in root.findall("thread"):
        listno = t.findtext("listno")
        name = _clean(t.findtext("name", ""))
        docs, sent_text, sent_doc = [], {}, {}
        senders = []
        for di, d in enumerate(t.findall("DOC"), start=1):
            sender = _clean(d.findtext("From", ""))
            received = _clean(d.findtext("Received", ""))
            senders.append((sender, received))
            body = []
            for s in d.findall(".//Sent"):
                txt = _clean(s.text or "")
                sid = s.get("id", "")
                if sid:
                    sent_text[sid] = txt
                    sent_doc[sid] = di
                if txt:
                    body.append(txt)
            docs.append(" ".join(body))
        threads[listno] = {"name": name, "docs": docs, "senders": senders,
                           "sent_text": sent_text, "sent_doc": sent_doc}
    return threads


def parse_annotations():
    root = ET.parse(RAW / "bc3_annotation.xml").getroot()
    anns = defaultdict(list)
    for t in root.findall("thread"):
        listno = t.findtext("listno")
        for a in t.findall("annotation"):
            summary = [_clean(s.text or "") for s in a.findall(".//summary/sent") if _clean(s.text or "")]
            labels = defaultdict(list)
            lab = a.find("labels")
            if lab is not None:
                for child in lab:
                    sid = child.get("id")
                    if sid:
                        labels[child.tag].append(sid)
            anns[listno].append({"desc": a.findtext("desc", ""), "summary": summary, "labels": labels})
    return anns


def consensus_sentences(annotations, tag, min_votes=2):
    """sentence ids labelled `tag` by >= min_votes annotators."""
    votes = Counter()
    for a in annotations:
        for sid in set(a["labels"].get(tag, [])):
            votes[sid] += 1
    return [sid for sid, v in votes.items() if v >= min_votes]


def sender_short(sender: str) -> str:
    # "Jacob Palme <jpalme@dsv.su.se>" -> "Jacob Palme"
    return _clean(sender.split("<")[0]) or sender


def main():
    corpus = parse_corpus()
    anns = parse_annotations()
    rows = []
    slice_counts = Counter()

    for listno, th in corpus.items():
        ann_list = anns.get(listno, [])
        if not ann_list:
            continue
        refs = SEP.join(d for d in th["docs"] if d.strip())
        n_refs = len([d for d in th["docs"] if d.strip()])
        name = th["name"]
        base = listno

        def add(slice_tag, suffix, query, answer):
            answer = _clean(answer)
            if not answer:
                return
            rows.append({
                "slice": slice_tag, "_id": f"{base}#{suffix}", "category": "Decision",
                "type": "", "reasoning": "", "n_refs": n_refs, "query_words": len(query.split()),
                "query": query, "answer": answer, "references_joined": refs,
            })
            slice_counts[slice_tag] += 1

        # E1_FACT — initiator + date (vector control)
        if th["senders"]:
            s0, d0 = th["senders"][0]
            add("E1_FACT", "init",
                f"Who initiated the email thread '{name}', and on what date?",
                f"{sender_short(s0)} initiated the thread on {d0}.")

        # E2_DECISION_SUMMARY — human extractive summary (annotator-0)
        summ = ann_list[0]["summary"]
        if summ:
            add("E2_DECISION_SUMMARY", "summary",
                f"Summarize the discussion in the thread '{name}': the key proposals, "
                f"the positions participants took, and the outcome.",
                " ".join(summ))

        # E3_PROPOSALS — consensus prop sentences (text + sender)
        prop_ids = consensus_sentences(ann_list, "prop")
        if prop_ids:
            parts = []
            for sid in sorted(prop_ids, key=lambda x: tuple(int(p) for p in x.split('.') if p.isdigit()) or (0,)):
                txt = th["sent_text"].get(sid, "")
                di = th["sent_doc"].get(sid)
                who = sender_short(th["senders"][di - 1][0]) if di and di <= len(th["senders"]) else ""
                if txt:
                    parts.append(f"{who}: {txt}" if who else txt)
            add("E3_PROPOSALS", "props",
                f"What proposals or suggestions were made in the thread '{name}', and by whom?",
                " | ".join(parts))

        # E4_POSITIONS — consensus subjective/opinion sentences (positions)
        subj_ids = consensus_sentences(ann_list, "subj")
        if subj_ids:
            parts = []
            for sid in sorted(subj_ids, key=lambda x: tuple(int(p) for p in x.split('.') if p.isdigit()) or (0,)):
                txt = th["sent_text"].get(sid, "")
                di = th["sent_doc"].get(sid)
                who = sender_short(th["senders"][di - 1][0]) if di and di <= len(th["senders"]) else ""
                if txt:
                    parts.append(f"{who}: {txt}" if who else txt)
            add("E4_POSITIONS", "positions",
                f"In the thread '{name}', what positions or opinions did participants express "
                f"(who was for or against), and on what?",
                " | ".join(parts))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {len(rows)} cases -> {OUT}")
    print("per-slice counts:")
    for s, n in sorted(slice_counts.items()):
        print(f"  {s:<22} {n}")
    print(f"threads used: {len(set(r['_id'].split('#')[0] for r in rows))}/{len(corpus)}")


if __name__ == "__main__":
    main()
