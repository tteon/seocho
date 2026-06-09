"""Regression for #134 — ExtractionStrategy must select its prompt from a
per-call category, not shared instance state. _LocalEngine reused one strategy
and set strategy.category per call, so two concurrent add() calls raced and one
could render with the other's category.
"""

from __future__ import annotations

import threading

from seocho.ontology import NodeDef, Ontology, P
from seocho.query.strategy import (
    CATEGORY_PROMPT_MAP,
    PRESET_PROMPTS,
    ExtractionStrategy,
)


def _strategy() -> ExtractionStrategy:
    onto = Ontology(name="t", nodes={"Doc": NodeDef(properties={"name": P(str)})})
    return ExtractionStrategy(onto, category="general")


def _expected(strategy: ExtractionStrategy, category: str, text: str):
    ctx = strategy.ontology.to_extraction_context()
    return PRESET_PROMPTS[CATEGORY_PROMPT_MAP[category]].render(ctx, text)


def test_render_uses_per_call_category() -> None:
    strategy = _strategy()
    fin = strategy.render("text", category="Financials")
    legal = strategy.render("text", category="Legal")

    assert fin == _expected(strategy, "Financials", "text")
    assert legal == _expected(strategy, "Legal", "text")
    assert fin != legal


def test_render_does_not_mutate_instance_category() -> None:
    strategy = _strategy()
    strategy.render("text", category="Financials")
    assert strategy.category == "general"  # untouched
    # falls back to the constructor default when no per-call category is given
    # (compare the system prompt — the part category drives)
    assert strategy.render("text")[0] == strategy.render("text", category="general")[0]


def test_concurrent_renders_do_not_cross_talk() -> None:
    strategy = _strategy()
    categories = ["Financials", "Legal", "Risk", "Governance"]
    expected = {c: _expected(strategy, c, "text") for c in categories}
    errors: list[str] = []

    def worker(cat: str) -> None:
        for _ in range(50):
            if strategy.render("text", category=cat) != expected[cat]:
                errors.append(cat)
                return

    threads = [threading.Thread(target=worker, args=(c,)) for c in categories * 4]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
