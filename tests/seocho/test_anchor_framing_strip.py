"""A leading source-framing clause is stripped before anchor-slot extraction.

Measured live (ADR-0213 records=0 diagnosis): on "In the narrative of 'An
Unsentimental Journey through Cornwall', which plant known as Erica vagans ...",
the intent extractor anchored on the quoted BOOK TITLE, so the Cypher matched no
node and retrieval returned 0 rows. The deterministic stripper drops only a
conservative leading framing clause (framing preposition + quoted title + comma)
so the anchor becomes the real subject; ordinary questions are untouched.
"""

from __future__ import annotations

from seocho.query.planner import _strip_framing_clause


def test_strips_narrative_framing_clause():
    q = ("In the narrative of 'An Unsentimental Journey through Cornwall', which "
         "plant known scientifically as Erica vagans is also referred to by "
         "another common name?")
    out = _strip_framing_clause(q)
    assert out.startswith("which plant"), out
    assert "Unsentimental Journey" not in out
    assert "Erica vagans" in out


def test_strips_common_framing_prepositions():
    for lead in [
        "According to \"The 2023 Annual Report\", ",
        "Based on 'Moby-Dick', ",
        "In “War and Peace”, ",
        "From 'the Q3 filing', ",
    ]:
        q = lead + "who is the main character described?"
        out = _strip_framing_clause(q)
        assert out == "who is the main character described?", (lead, out)


def test_plain_question_is_untouched():
    q = "Which common name is Erica vagans also known by?"
    assert _strip_framing_clause(q) == q


def test_no_quoted_title_is_untouched():
    # "In Cornwall" has no quoted title -> it is a real locative, not framing.
    q = "In Cornwall, which plants grow on the serpentine soil?"
    assert _strip_framing_clause(q) == q


def test_bare_title_with_nothing_after_is_not_stripped_to_empty():
    q = "In the report 'Q3 Earnings'."
    # remainder after the clause is empty/trivial -> keep the original
    assert _strip_framing_clause(q) == q


def test_planner_uses_the_stripper():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "src" / "seocho" / "query" / "planner.py").read_text()
    assert "_strip_framing_clause(question)" in src, (
        "plan() must de-frame the question before intent extraction"
    )
