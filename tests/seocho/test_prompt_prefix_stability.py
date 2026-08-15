"""The intent-extraction system prompt must be byte-stable up to its volatile tail.

ADR-0148 requires stable prompt sections to precede volatile ones, because a KV
prefix is reusable only as far as the prompt is byte-identical. This was not
enforced anywhere, and it had regressed: question-scoped schema hints sat just
after the invariant header, so the ~2.7 KB ontology body — identical for every
question on one ontology — fell *after* the first divergence and was re-prefilled
on every call.

Measured on vLLM 0.27.1 / Qwen3-0.6B before the fix: the `plan` stage reported
`stable_prefix_chars = 0` and 8-14% cross-question prefix-cache hits, against 99%
when the same question repeated.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from seocho import Ontology  # noqa: E402
from seocho.query.cypher_builder import CypherBuilder  # noqa: E402

HINT_MARKER = "Question-scoped schema hints"

_QUESTIONS = [
    "Which accounts transferred money to B2?",
    "Which institution holds account C3?",
    "Which accounts form a transfer cycle?",
    "How many accounts are held at Northbank?",
]


@pytest.fixture
def builder() -> CypherBuilder:
    return CypherBuilder(
        Ontology.from_dict(
            {
                "name": "prefixtest",
                "nodes": {
                    "Account": {"properties": {"name": "string", "balance": "float"}},
                    "Institution": {"properties": {"name": "string"}},
                },
                "relationships": {
                    "TRANSFER": {"source": "Account", "target": "Account"},
                    "HELD_AT": {"source": "Account", "target": "Institution"},
                },
            }
        )
    )


def _prompts(builder: CypherBuilder) -> list[str]:
    return [
        builder.intent_extraction_prompt(schema_hints=builder.derive_schema_hints(q))
        for q in _QUESTIONS
    ]


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def test_everything_before_the_hint_section_is_identical(builder):
    """The reusable prefix is everything up to the question-scoped hints."""
    prompts = _prompts(builder)
    heads = []
    for prompt in prompts:
        assert HINT_MARKER in prompt, "hint section missing; the fixture must produce hints"
        heads.append(prompt[: prompt.index(HINT_MARKER)])

    assert len(set(heads)) == 1, (
        "the system prompt diverges before its volatile tail, so the ontology body "
        "cannot be cached; put question-derived content last (ADR-0148)"
    )


def test_hints_are_the_last_section(builder):
    """Nothing stable may follow the hints, or it lands past the divergence."""
    for prompt in _prompts(builder):
        tail = prompt[prompt.index(HINT_MARKER) :]
        # The hint block is a run of "- " lines and nothing else.
        body = [line for line in tail.splitlines()[1:] if line.strip()]
        assert body, "hint section rendered empty"
        assert all(line.startswith("- ") for line in body), (
            f"content after the hint section would be uncacheable: {body}"
        )


def test_stable_prefix_dominates_the_prompt(builder):
    """Guards against the hints growing until caching stops paying off."""
    prompts = _prompts(builder)
    shortest = min(len(p) for p in prompts)
    for other in prompts[1:]:
        shared = _common_prefix_len(prompts[0], other)
        assert shared / shortest > 0.75, (
            f"only {shared}/{shortest} chars shared across questions"
        )


def test_repeating_one_question_is_byte_identical(builder):
    """Hint derivation must be deterministic, or even a repeat cannot reuse."""
    once = builder.intent_extraction_prompt(
        schema_hints=builder.derive_schema_hints(_QUESTIONS[0])
    )
    twice = builder.intent_extraction_prompt(
        schema_hints=builder.derive_schema_hints(_QUESTIONS[0])
    )
    assert once == twice
