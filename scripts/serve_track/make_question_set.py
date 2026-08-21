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
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCHEMA_VERSION = 1

# A closed world: closed because S5 (absence) is only meaningful when "not
# present" is knowable rather than "not retrieved".
#
# The world is bigger than it was, on purpose. The first run put vector, graph
# and both at 12/12, 11/12 and 12/12 — a ceiling, not a tie (seocho-eer). Four
# suppliers over three plants cannot separate anything: the deepest chain was
# three hops and the widest aggregation covered two rows, both of which a model
# does in one glance regardless of how the context is shaped.
#
# Scaled to 12 suppliers, 6 plants, 6 products and 3 regions, with a MATERIAL
# layer inserted below suppliers so chains reach five hops. Still a closed
# world, still one fact per passage, still hand-checkable — every mapping below
# is a literal table, not a generator, so a reader can verify any answer by eye.
_MATERIALS = ["Bauxite", "Cobalt", "Silica", "Titanium"]
_SUPPLIERS = [
    "Aurora Metals", "Borex Chemical", "Cedar Plastics", "Delta Alloys",
    "Everline Mining", "Fairweather Ore", "Granite Supply", "Halcyon Resources",
    "Ironvale Group", "Juniper Materials", "Kestrel Minerals", "Lumen Industrial",
]
_PLANTS = [
    "Northgate Plant", "Southport Plant", "Eastfield Plant",
    "Westbrook Plant", "Highmoor Plant", "Larkspur Plant",
]
_PRODUCTS = ["Model K1", "Model K2", "Model L9", "Model M3", "Model P7", "Model R4"]
_REGIONS = ["Norland", "Sudmark", "Verrat"]

# material -> supplier, the layer that makes four-hop chains possible.
#
# Grouped so every supplier of a given material feeds plants whose products are
# sold in the SAME region. That is not decoration: "in which region is the
# product made from Silica sold?" has no single answer unless the material's
# chains converge, and the first draft of this table did not converge — Silica
# reached both Sudmark (via Cedar Plastics) and Norland (via Granite Supply).
# A dict built from the pairs silently kept the last supplier per material and
# produced an answer that looked right and was unverifiable by hand, which is
# the failure this whole set exists to avoid.
#
#   Norland plants  : Northgate, Eastfield, Highmoor
#   Sudmark plants  : Southport, Larkspur
#   Verrat plants   : Westbrook
_MINES: List[Tuple[str, str]] = [
    # -> Norland
    ("Bauxite", "Aurora Metals"),      # Northgate
    ("Bauxite", "Everline Mining"),    # Northgate
    ("Bauxite", "Delta Alloys"),       # Eastfield
    # -> Norland, the widest fan-out in the set
    ("Cobalt", "Borex Chemical"),      # Northgate
    ("Cobalt", "Granite Supply"),      # Northgate
    ("Cobalt", "Ironvale Group"),      # Northgate
    ("Cobalt", "Kestrel Minerals"),    # Highmoor
    # -> Sudmark
    ("Silica", "Cedar Plastics"),      # Southport
    ("Silica", "Fairweather Ore"),     # Southport
    ("Silica", "Lumen Industrial"),    # Larkspur
    # -> Verrat
    ("Titanium", "Halcyon Resources"), # Westbrook
    ("Titanium", "Juniper Materials"), # Westbrook
]

# supplier -> plant -> product -> region, one edge per fact, one fact per passage.
# Northgate now has five suppliers rather than two, so S4 aggregation has to
# count across a fan-out a model cannot hold in one glance.
_SUPPLIES: List[Tuple[str, str]] = [
    ("Aurora Metals", "Northgate Plant"),
    ("Borex Chemical", "Northgate Plant"),
    ("Everline Mining", "Northgate Plant"),
    ("Granite Supply", "Northgate Plant"),
    ("Ironvale Group", "Northgate Plant"),
    ("Cedar Plastics", "Southport Plant"),
    ("Fairweather Ore", "Southport Plant"),
    ("Delta Alloys", "Eastfield Plant"),
    ("Halcyon Resources", "Westbrook Plant"),
    ("Juniper Materials", "Westbrook Plant"),
    ("Kestrel Minerals", "Highmoor Plant"),
    ("Lumen Industrial", "Larkspur Plant"),
]
_ASSEMBLES: List[Tuple[str, str]] = [
    ("Northgate Plant", "Model K1"),
    ("Southport Plant", "Model K2"),
    ("Eastfield Plant", "Model L9"),
    ("Westbrook Plant", "Model M3"),
    ("Highmoor Plant", "Model P7"),
    ("Larkspur Plant", "Model R4"),
]
_SOLD_IN: List[Tuple[str, str]] = [
    ("Model K1", "Norland"),
    ("Model K2", "Sudmark"),
    ("Model L9", "Norland"),
    ("Model M3", "Verrat"),
    ("Model P7", "Norland"),
    ("Model R4", "Sudmark"),
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
    # For list answers: the items that must NOT appear. A set answer cannot be
    # scored by substring containment (order and conjunctions break it) and an
    # LLM judge over-rejects supporting detail, so the complement is carried
    # explicitly and the check stays deterministic.
    excluded: List[str] = field(default_factory=list)

    def to_row(self) -> Dict[str, Any]:
        # Content-derived, so it is stable across reordering and reruns. Without
        # an id every downstream row is anonymous and results cannot be joined
        # back to the question that produced them.
        digest = hashlib.sha256(self.question.encode("utf-8")).hexdigest()[:8]
        return {
            "schema_version": SCHEMA_VERSION,
            "id": f"{self.stratum}-{digest}",
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
            "excluded": self.excluded,
        }


def _supply_passage(s: str, p: str) -> str:
    return f"{s} supplies raw material to {p}."


def _assemble_passage(p: str, prod: str) -> str:
    return f"{p} assembles {prod}."


def _sold_passage(prod: str, r: str) -> str:
    return f"{prod} is sold in {r}."


def _mine_passage(material: str, supplier: str) -> str:
    # Phrased material-first so the gold path is a connected chain from the
    # question's starting point. "X extracts Y" would make the first edge point
    # away from the material and break the four-hop path.
    return f"{material} is extracted by {supplier}."


def _all_passages() -> List[str]:
    return (
        [_mine_passage(m, s) for m, s in _MINES]
        + [_supply_passage(s, p) for s, p in _SUPPLIES]
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

    # S3b — four hops, starting one layer lower. The first run saturated at
    # three (3/3 for every arm), so depth had no room to show an effect.
    for material in ("Bauxite", "Silica", "Titanium"):
        # Any supplier of the material reaches the same region by construction;
        # take the first and assert the convergence rather than trusting it.
        chain_suppliers = [s for m, s in _MINES if m == material]
        regions = {dict(_SOLD_IN)[dict(_ASSEMBLES)[dict(_SUPPLIES)[s]]] for s in chain_suppliers}
        assert len(regions) == 1, f"{material} chains diverge to {regions}; question is ambiguous"
        supplier = chain_suppliers[0]
        plant = dict(_SUPPLIES)[supplier]
        prod = dict(_ASSEMBLES)[plant]
        region = dict(_SOLD_IN)[prod]
        items.append(Item(
            stratum="S3b_four_hop",
            question=f"In which region is the product made from {material} sold?",
            answer=region, corpus=corpus, hops=4, dispersion=4, answer_type="joined",
            prediction="graph; if S3 is flat and S3b is not, depth is the axis after all",
            gold_edges=[[material, "EXTRACTED_BY", supplier], [supplier, "SUPPLIES", plant],
                        [plant, "ASSEMBLES", prod], [prod, "SOLD_IN", region]]))

    # S4 — fan-out. Passages repeat the plant name; the graph states it once.
    items.append(Item(
        stratum="S4_aggregation", question="How many suppliers supply Northgate Plant?",
        answer="5", corpus=corpus, hops=1, dispersion=5, answer_type="aggregate",
        prediction="graph via redundancy removal; attention should be FLAT, not on relations",
        gold_edges=[[s, "SUPPLIES", "Northgate Plant"] for s, p in _SUPPLIES if p == "Northgate Plant"]))
    items.append(Item(
        stratum="S4_aggregation", question="How many products are sold in Norland?",
        answer="3", corpus=corpus, hops=1, dispersion=3, answer_type="aggregate",
        prediction="graph via redundancy removal",
        gold_edges=[[prod, "SOLD_IN", "Norland"] for prod, r in _SOLD_IN if r == "Norland"]))
    items.append(Item(
        stratum="S4_aggregation", question="How many suppliers extract Cobalt?",
        answer="4", corpus=corpus, hops=1, dispersion=4, answer_type="aggregate",
        prediction="graph; the widest fan-out in the material layer",
        gold_edges=[["Cobalt", "EXTRACTED_BY", s] for m, s in _MINES if m == "Cobalt"]))

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

    # S7b — near-miss names rather than recombined ones. The first run's S7
    # distractors reused real entity names in false pairings, which a model can
    # reject by checking the pair. These differ from a real supplier by one
    # token, so rejecting them needs the name itself to be resolved.
    near_miss = [
        "Aurora Metalworks supplies raw material to Southport Plant.",
        "Delta Alloy supplies raw material to Northgate Plant.",
        "Cedar Plastic supplies raw material to Eastfield Plant.",
        "Ironvale Grouping supplies raw material to Larkspur Plant.",
        "Kestrel Mineral supplies raw material to Westbrook Plant.",
    ]
    items.append(Item(
        stratum="S7b_near_miss", question="Which plant does Delta Alloys supply?",
        answer="Eastfield Plant", corpus=corpus + near_miss, hops=1, dispersion=1,
        answer_type="extractive",
        prediction="graph, and for a reason that IS interesting: typed node identity "
                   "separates 'Delta Alloys' from 'Delta Alloy' where a passage set has "
                   "only the string. A graph win here is entity resolution, not structure.",
        gold_edges=[["Delta Alloys", "SUPPLIES", "Eastfield Plant"]]))

    # S8 — negation and closed-world, promoted from a one-item hypothesis.
    # The first run's only graph LOSS was S5's "which is not sold in Norland":
    # the graph arm received the single relevant edge and answered NOT STATED,
    # while the vector arm held every sold-in statement and did the exclusion.
    # A triple list asserts positives; a negation needs the complement, which is
    # exactly what relevance filtering removes (seocho-2gq). One item could not
    # carry that, so the mechanism gets its own stratum with the prediction
    # stated against the graph.
    norland = [prod for prod, r in _SOLD_IN if r == "Norland"]
    not_norland = [prod for prod, r in _SOLD_IN if r != "Norland"]
    items.append(Item(
        stratum="S8_negation",
        question=f"Which of {', '.join(_PRODUCTS)} is not sold in Norland?",
        answer=", ".join(not_norland), corpus=corpus, hops=1, dispersion=len(_SOLD_IN),
        answer_type="negation", excluded=norland,
        prediction="VECTOR. Answering needs the complement, and a relevance-filtered "
                   "triple list drops it. If graph wins here the seocho-2gq hypothesis dies.",
        gold_edges=[[prod, "SOLD_IN", dict(_SOLD_IN)[prod]] for prod in not_norland]))
    items.append(Item(
        stratum="S8_negation",
        question="Which plants do NOT assemble a product sold in Norland?",
        answer=", ".join(sorted(p for p, prod in _ASSEMBLES if prod not in norland)),
        corpus=corpus, hops=2, dispersion=len(_ASSEMBLES) + len(_SOLD_IN),
        answer_type="negation",
        excluded=sorted(p for p, prod in _ASSEMBLES if prod in norland),
        prediction="vector; negation over a join is the hardest case for a triple list",
        gold_edges=[[p, "ASSEMBLES", prod] for p, prod in _ASSEMBLES if prod not in norland]))
    items.append(Item(
        stratum="S8_negation",
        question="Which suppliers do NOT supply Northgate Plant?",
        answer=", ".join(sorted(s for s in _SUPPLIERS if dict(_SUPPLIES).get(s) != "Northgate Plant")),
        corpus=corpus, hops=1, dispersion=len(_SUPPLIES), answer_type="negation",
        excluded=sorted(s for s, p in _SUPPLIES if p == "Northgate Plant"),
        prediction="vector; the complement is large, so a filtered subgraph loses most of it",
        gold_edges=[[s, "SUPPLIES", p] for s, p in _SUPPLIES if p != "Northgate Plant"]))

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
