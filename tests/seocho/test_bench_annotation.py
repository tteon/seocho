"""The derived strata must be derived, not assumed.

The hop count here is a *proxy* — triples parsed out of a free-text field. A
proxy that silently degrades is worse than no proxy, because the analysis keeps
running and the strata quietly stop meaning anything. These tests pin the two
ways it could degrade: the regex swallowing ordinary prose, and Medical (which
has no triples at all) being merged into the hop axis as though every item were
single-hop.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "annotate_graphrag_bench",
    _ROOT / "scripts" / "serve_track" / "annotate_graphrag_bench.py",
)
ann = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ann
_spec.loader.exec_module(ann)


def _row(**kw):
    base = {
        "id": "X-1", "source": "S", "question": "q", "answer": "a",
        "question_type": "Fact Retrieval", "evidence": "one statement.",
    }
    base.update(kw)
    return base


def test_triples_are_counted_from_the_triple_field():
    row = _row(
        question_type="Complex Reasoning",
        evidence_triple="(a, rel, b). (b, rel2, c).",
        evidence="first.; second.",
    )
    out = ann.annotate(row, "novel")
    assert out["strata"]["hops"] == 2
    assert out["gold_edges"] == [["a", "rel", "b"], ["b", "rel2", "c"]]


def test_parenthetical_prose_is_not_counted_as_a_triple():
    """A loose pattern would inflate hops off ordinary writing."""
    row = _row(evidence_triple="Basal cell carcinoma (BCC) is common (see chapter 4).")
    assert ann.annotate(row, "novel")["strata"]["hops"] is None


def test_medical_never_reports_a_hop_count():
    """Medical has no triples; a 0 here would read as single-hop and pool wrongly."""
    row = _row(evidence_triple="(a, rel, b).")
    out = ann.annotate(row, "medical")
    assert out["strata"]["hops"] is None
    assert out["strata"]["hops_source"] == "unavailable"


def test_absent_hops_are_none_not_zero():
    out = ann.annotate(_row(), "novel")
    assert out["strata"]["hops"] is None, "absent must not be representable as 0"


def test_dispersion_counts_semicolon_separated_statements():
    row = _row(evidence="one.; two.; three.")
    out = ann.annotate(row, "medical")
    assert out["strata"]["dispersion"] == 3
    assert out["corpus"] == ["one.", "two.", "three."]


def test_blank_statements_do_not_inflate_dispersion():
    row = _row(evidence="one.;  ; two.;")
    assert ann.annotate(row, "novel")["strata"]["dispersion"] == 2


def test_unknown_question_type_is_labelled_not_guessed():
    out = ann.annotate(_row(question_type="Brand New Type"), "novel")
    assert out["strata"]["answer_type"] == "unknown"
    assert out["strata"]["stratum"] == "GB_unknown"


def test_every_known_question_type_maps_to_an_axis():
    for qtype in ("Fact Retrieval", "Complex Reasoning",
                  "Contextual Summarize", "Creative Generation"):
        assert ann._TYPE_AXIS[qtype] != "unknown"


def test_list_shaped_fields_are_parsed():
    """The HuggingFace release ships these as lists, not strings.

    Accepting only the string shape made the annotator report "0 of 2010 carry
    a derived hop count" — which reads as a fact about GraphRAG-Bench rather
    than a parse failure, and points at the opposite of the truth. Novel yields
    triples for 2,009 of 2,010 items.
    """
    row = _row(
        question_type="Complex Reasoning",
        evidence_triple=["(a, rel, b)", "(b, rel2, c)"],
        evidence=["first statement.", "second statement."],
    )
    out = ann.annotate(row, "novel")
    assert out["strata"]["hops"] == 2
    assert out["gold_edges"] == [["a", "rel", "b"], ["b", "rel2", "c"]]
    assert out["strata"]["dispersion"] == 2


def test_string_shaped_fields_still_work():
    """The older local copy joined them with semicolons; both must parse."""
    row = _row(question_type="Complex Reasoning",
               evidence_triple="(a, rel, b). (b, rel2, c).",
               evidence="first.; second.")
    out = ann.annotate(row, "novel")
    assert out["strata"]["hops"] == 2
    assert out["strata"]["dispersion"] == 2
