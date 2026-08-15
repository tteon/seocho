"""A stratified question set for the graph-vs-vector-vs-both comparison.

Why this exists rather than "just use GraphRAG-Bench". GraphRAG-Bench gives real
textbook corpora across 16 disciplines and is the right external-validity check,
but its items carry only ``Question`` / ``Answer`` / ``Rationale`` /
``Level-1 Topic`` / ``Level-2 Topic`` and a question-type tag. There is **no gold
knowledge graph, no gold triples, and no supporting-fact ids**, so hop count and
passage dispersion are not derivable from it. Those two are exactly what makes
the *when* legible, so pooling over GraphRAG-Bench yields the third tie the
repository has already measured twice (ADR-0112, ADR-0154).

Division of labour:
  this set          strata exact by construction  -> carries the mechanism claim
  GraphRAG-Bench    real corpora, no strata       -> guards against "works only on your toy"

The strata are not difficulty tiers. Each one isolates a *different reason* a
graph context could beat a passage context, so that a win can be attributed
rather than merely observed. Each carries a falsifiable prediction; a stratum
that comes out flat is a result, not a failure.

  S1 extractive        1 hop, 1 passage. Both forms contain the fact outright.
                       PREDICT tie, or vector by a nose on token overhead.
                       If graph wins here, the effect is not structure — suspect
                       retrieval precision or serialization luck.

  S2 joined            2 hops, facts in 2 different passages. Vector must
                       retrieve both AND the model must compose across distant
                       tokens; the graph states the path.
                       PREDICT graph. Attention should concentrate on the
                       relation tokens. If it wins with FLAT attention, the
                       mechanism is redundancy removal, not stated structure.

  S3 deep join         3 hops, 3 passages. Same mechanism, longer chain.
                       PREDICT the S2 effect grows. If it does not, composition
                       depth is not the axis and S2's win came from elsewhere.

  S4 aggregation       1 hop, high fan-out ("how many X ..."). Passages are
                       redundant; the graph is deduplicated.
                       PREDICT graph, via redundancy removal. Attention should
                       be FLAT across the subgraph — that is the signature that
                       separates this from S2.

  S5 absence           "which X did NOT ...". A passage set cannot represent
                       absence; a closed-world graph can.
                       PREDICT graph decisively. This is a representational
                       property, not an attention one — if attention analysis
                       "explains" it, the analysis is overfitting.

  S6 ambiguous entity  Two distinct entities share a surface name. The graph
                       separates them by node identity; passages conflate them.
                       PREDICT graph. Vector errors should be *confusions*, not
                       misses — check the error type, not just the score.

  S7 distractor        1 hop, 1 passage, but many near-identical passages.
                       Controls for the boring explanation: if graph beats
                       vector here, "graph wins" is really "retrieval precision
                       wins", and S2-S4 need re-reading.

Output is the shape ``scripts/benchmarks/graphrag_bench.py`` already accepts
(``corpus`` / ``question`` / ``answer``), plus a ``strata`` block the runner
ignores and the analysis groups by. Nothing here needs a model or a network.

Usage:
    python scripts/serve_track/make_question_set.py --out outputs/serve_track/questions.jsonl
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCHEMA_VERSION = 1

# A deliberately small, closed world. Small because every stratum must be exact
# by construction and hand-checkable; closed because S5 (absence) is only
# meaningful when "not present" is knowable rather than "not retrieved".
_SUPPLIERS = ["Aurora Metals", "Borex Chemical", "Cedar Plastics", "Delta Alloys"]
_PLANTS = ["Northgate Plant", "Southport Plant", "Eastfield Plant"]
_PRODUCTS = ["Model K1", "Model K2", "Model L9"]
_REGIONS = ["Norland", "Sudmark"]

# supplier -> plant -> product -> region, one edge per fact, one fact per passage.
_SUPPLIES: List[Tuple[str, str]] = [
    ("Aurora Metals", "Northgate Plant"),
    ("Borex Chemical", "Northgate Plant"),
    ("Cedar Plastics", "Southport Plant"),
    ("Delta Alloys", "Eastfield Plant"),
]
_ASSEMBLES: List[Tuple[str, str]] = [
    ("Northgate Plant", "Model K1"),
    ("Southport Plant", "Model K2"),
    ("Eastfield Plant", "Model L9"),
]
_SOLD_IN: List[Tuple[str, str]] = [
    ("Model K1", "Norland"),
    ("Model K2", "Sudmark"),
    ("Model L9", "Norland"),
]
# S6: one surface name, two distinct entities. The graph gives them separate
# node identities; a passage set has only the string.
_AMBIGUOUS = [
    ("Aurora Metals (Norland)", "Northgate Plant"),
    ("Aurora Metals (Sudmark)", "Eastfield Plant"),
]


@dataclass
class Item:
    stratum: str
    question: str
    answer: str
    corpus: List[str]
    hops: int
    dispersion: int          # distinct passages holding the needed facts
    answer_type: str         # extractive | joined | aggregate | absence | disambiguation
    prediction: str          # which form should win, and why — stated in advance
    gold_edges: List[List[str]] = field(default_factory=list)

    def to_row(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "question": self.question,
            "answer": self.answer,
            "corpus": self.corpus,
            "strata": {
                "stratum": self.stratum,
                "hops": self.hops,
                "dispersion": self.dispersion,
                "answer_type": self.answer_type,
                "prediction": self.prediction,
            },
            "gold_edges": self.gold_edges,
        }


def _supply_passage(s: str, p: str) -> str:
    return f"{s} supplies raw material to {p}."


def _assemble_passage(p: str, prod: str) -> str:
    return f"{p} assembles {prod}."


def _sold_passage(prod: str, r: str) -> str:
    return f"{prod} is sold in {r}."


def _all_passages() -> List[str]:
    return (
        [_supply_passage(s, p) for s, p in _SUPPLIES]
        + [_assemble_passage(p, prod) for p, prod in _ASSEMBLES]
        + [_sold_passage(prod, r) for prod, r in _SOLD_IN]
    )


def build() -> List[Item]:
    items: List[Item] = []
    corpus = _all_passages()

    # S1 — the fact is written down, once, in one place.
    for s, p in _SUPPLIES[:2]:
        items.append(Item(
            stratum="S1_extractive", question=f"Which plant does {s} supply?",
            answer=p, corpus=corpus, hops=1, dispersion=1, answer_type="extractive",
            prediction="tie or vector; both forms state the fact outright",
            gold_edges=[[s, "SUPPLIES", p]]))

    # S2 — two facts, two passages, one join the model must perform.
    for s, p in _SUPPLIES[:2]:
        prod = dict(_ASSEMBLES)[p]
        items.append(Item(
            stratum="S2_joined", question=f"Which product does {s} contribute to?",
            answer=prod, corpus=corpus, hops=2, dispersion=2, answer_type="joined",
            prediction="graph; the path is stated, and vector must compose across passages",
            gold_edges=[[s, "SUPPLIES", p], [p, "ASSEMBLES", prod]]))

    # S3 — same mechanism, one hop deeper.
    for s, p in _SUPPLIES[:3]:
        prod = dict(_ASSEMBLES)[p]
        region = dict(_SOLD_IN)[prod]
        items.append(Item(
            stratum="S3_deep_join", question=f"In which region are {s}'s materials ultimately sold?",
            answer=region, corpus=corpus, hops=3, dispersion=3, answer_type="joined",
            prediction="graph, and the S2 margin should grow with depth",
            gold_edges=[[s, "SUPPLIES", p], [p, "ASSEMBLES", prod], [prod, "SOLD_IN", region]]))

    # S4 — fan-out. Passages repeat the plant name; the graph states it once.
    items.append(Item(
        stratum="S4_aggregation", question="How many suppliers supply Northgate Plant?",
        answer="2", corpus=corpus, hops=1, dispersion=2, answer_type="aggregate",
        prediction="graph via redundancy removal; attention should be FLAT, not on relations",
        gold_edges=[[s, "SUPPLIES", "Northgate Plant"] for s, p in _SUPPLIES if p == "Northgate Plant"]))
    items.append(Item(
        stratum="S4_aggregation", question="How many products are sold in Norland?",
        answer="2", corpus=corpus, hops=1, dispersion=2, answer_type="aggregate",
        prediction="graph via redundancy removal",
        gold_edges=[[prod, "SOLD_IN", "Norland"] for prod, r in _SOLD_IN if r == "Norland"]))

    # S5 — absence. A passage set has no way to say "there is no such edge".
    items.append(Item(
        stratum="S5_absence", question="Which plant has no supplier listed?",
        answer="none", corpus=corpus, hops=1, dispersion=0, answer_type="absence",
        prediction="graph decisively; closed-world, and NOT an attention effect",
        gold_edges=[]))
    items.append(Item(
        stratum="S5_absence", question="Which of Model K1, Model K2, Model L9 is not sold in Norland?",
        answer="Model K2", corpus=corpus, hops=1, dispersion=3, answer_type="absence",
        prediction="graph; vector must prove a negative from an open passage set",
        gold_edges=[["Model K2", "SOLD_IN", "Sudmark"]]))

    # S6 — one surface name, two node identities.
    amb_corpus = corpus + [_supply_passage(s, p) for s, p in _AMBIGUOUS]
    items.append(Item(
        stratum="S6_ambiguous", question="Which plant does Aurora Metals (Sudmark) supply?",
        answer="Eastfield Plant", corpus=amb_corpus, hops=1, dispersion=1,
        answer_type="disambiguation",
        prediction="graph; vector errors should be CONFUSIONS with the other Aurora, not misses",
        gold_edges=[["Aurora Metals (Sudmark)", "SUPPLIES", "Eastfield Plant"]]))

    # S7 — the control for the boring explanation. Answer is extractive and
    # single-passage, but buried among near-identical distractors. If graph wins
    # HERE, then "graph wins" is really "retrieval precision wins" and S2-S4
    # need re-reading.
    noise = [f"{s} supplies raw material to {p}." for s in _SUPPLIERS for p in _PLANTS]
    items.append(Item(
        stratum="S7_distractor", question="Which plant does Delta Alloys supply?",
        answer="Eastfield Plant", corpus=corpus + noise, hops=1, dispersion=1,
        answer_type="extractive",
        prediction="tie; a graph win here means the effect is retrieval precision, not structure",
        gold_edges=[["Delta Alloys", "SUPPLIES", "Eastfield Plant"]]))

    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=Path("outputs/serve_track/questions.jsonl"))
    args = parser.parse_args()

    items = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item.to_row(), ensure_ascii=False) + "\n")

    counts: Dict[str, int] = {}
    for item in items:
        counts[item.stratum] = counts.get(item.stratum, 0) + 1
    print(f"wrote {len(items)} items to {args.out}")
    for stratum in sorted(counts):
        print(f"  {stratum:18s} n={counts[stratum]}")
    print("\nn is small by design — these are hand-checkable probes, not a power "
          "calculation. Scale a stratum only after it shows a signal.")


if __name__ == "__main__":
    main()
