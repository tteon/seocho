"""The extraction prompt must carry the ontology's requirements, not just its schema.

Keet's micro-level methodologies (OntoSpec, OD101, DiDOn) make the point that
authoring an ontology begins with purpose, competency questions, and explicit
modelling decisions, and that those determine the axioms rather than preceding
them. The extraction prompt shipped only the formalised artefact — a list of
classes, properties and relations — so the model received the output of that
analysis with none of the analysis. `Ontology(description=...)` was accepted,
stored, and dropped at the exact point it would have helped.

These pin the three requirement channels and, just as importantly, that an
ontology declaring none of them renders exactly as it did before.
"""

from __future__ import annotations

from seocho import NodeDef, Ontology, P
from seocho.index.extraction_engine import CanonicalExtractionEngine


class _FakeLLM:
    provider = "fake"
    model = "fake"


def _render(ontology: Ontology) -> str:
    engine = CanonicalExtractionEngine(llm=_FakeLLM(), ontology=ontology, enforcement="guided")
    system, _ = engine._render_extraction_prompts(
        text="x", category="general", metadata=None, extra_context=None
    )
    return system


def _ontology(**kwargs) -> Ontology:
    return Ontology(
        name="t",
        nodes={"A": NodeDef(properties={"name": P(str, unique=True)})},
        **kwargs,
    )


def test_the_purpose_reaches_the_prompt():
    """`description` was accepted and stored and never rendered."""
    system = _render(_ontology(description="Recover which decision is CURRENT."))
    assert "Recover which decision is CURRENT." in system


def test_competency_questions_reach_the_prompt():
    """They state what the graph must ANSWER, which a type list cannot.

    This package already scores coverage against competency questions
    (`competency_question_report`); until now nothing told the extractor what
    they were.
    """
    system = _render(_ontology(annotations={
        "competency_questions": ["Which value is currently applied?"]}))
    assert "must be able to answer" in system
    assert "Which value is currently applied?" in system


def test_modelling_decisions_reach_the_prompt():
    """Choices the schema cannot express: attribute vs class, relation direction."""
    system = _render(_ontology(annotations={
        "modelling_decisions": ["SUPERSEDES runs newer -> older."]}))
    assert "Modelling decisions to honour" in system
    assert "SUPERSEDES runs newer -> older." in system


def test_an_ontology_without_requirements_renders_as_before():
    """Additive: no declared requirements means no new prompt sections."""
    system = _render(_ontology())
    for marker in ("- Purpose:", "must be able to answer", "Modelling decisions to honour"):
        assert marker not in system


def test_annotations_default_to_an_empty_mapping():
    assert _ontology().annotations == {}


def test_the_extraction_context_stays_a_string_mapping():
    """`to_extraction_context` is declared Dict[str, str] and callers join it.

    Adding the requirement channels as lists made `"\\n".join(ctx.values())`
    raise a TypeError in a test three modules away — the contract is load-bearing.
    """
    ontology = _ontology(
        description="p",
        annotations={"competency_questions": ["q"], "modelling_decisions": ["d"]},
    )
    context = ontology.to_extraction_context()
    assert all(isinstance(v, str) for v in context.values()), context
    "\n".join(context.values())
