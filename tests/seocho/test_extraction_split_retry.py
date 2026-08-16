"""A prose-not-JSON extraction failure is recovered by splitting, not discarded.

A hosted reasoning model spends its output budget thinking and, on a large
chunk, emits reasoning prose with no JSON at all -- measured live on MARA
MiniMax-M2.7 ("no JSON object found (head: 'Let me analyze this text
carefully...')"). The same model emits clean JSON on a smaller input.

Before this, such a chunk fell straight to the capitalized-token heuristic,
which manufactures Entity/MENTIONS structure and loses every real relationship.
Now the pipeline halves the chunk and extracts each part first, so the real
structure is recovered.

Tested deterministically with a fake extractor that raises above a size
threshold and returns JSON below it -- the exact shape of the live failure,
without the live cost or non-determinism.
"""

from __future__ import annotations

import pytest

from seocho import NodeDef, Ontology, P, RelDef
from seocho.index.pipeline import IndexingPipeline


class _SizeGatedExtractor:
    """Raises on text longer than `limit`, returns JSON below it.

    Mirrors a reasoning model that reasons past its token budget on large
    inputs. Each successful call yields one node named after the first word,
    so a caller can tell how many sub-extractions happened.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.calls = []

    def extract(self, text, *, category="general", metadata=None):
        self.calls.append(len(text))
        if len(text) > self.limit:
            raise ValueError("no JSON object found in response (reasoning prose)")
        first = (text.strip().split() or ["x"])[0]
        return {
            "nodes": [{"id": first, "label": "Entity",
                       "properties": {"name": first}}],
            "relationships": [],
        }

    # The pipeline references these on the extraction engine.
    def _normalize_relationship_type(self, raw):
        return raw


def _ontology():
    return Ontology(
        name="split",
        nodes={"Entity": NodeDef(properties={"name": P(str, unique=True), "kind": P(str)},
                                 identity_keys=["name"])},
        relationships={"RELATED_TO": RelDef(source="Entity", target="Entity",
                                            description="A relationship.")},
    )


def _pipe(limit):
    pipe = IndexingPipeline(ontology=_ontology(), graph_store=object(), llm=object())
    pipe._graph_extraction = _SizeGatedExtractor(limit)
    return pipe


def test_split_recovers_where_the_whole_chunk_failed():
    """The whole text is too big; each half is small enough."""
    # Halves must each exceed the 800-char split floor and stay under `limit`,
    # so the whole (~1800) fails and each half (~900) succeeds.
    pipe = _pipe(limit=1000)
    text = ("Alpha " * 150).strip() + "\n\n" + ("Bravo " * 150).strip()
    out = pipe._extract_by_splitting(text, category="general", metadata=None)
    assert out is not None, "splitting a too-large chunk should recover structure"
    names = {n["properties"]["name"] for n in out["nodes"]}
    assert names == {"Alpha", "Bravo"}, "both halves must be extracted and merged"


def test_returns_none_below_the_floor():
    """A small chunk that still fails is not size-related; do not loop."""
    pipe = _pipe(limit=0)  # everything fails
    out = pipe._extract_by_splitting("short text", category="general", metadata=None)
    assert out is None, "below the floor, splitting cannot help"


def test_recursion_is_bounded():
    """Everything fails; recursion must terminate and return None, not hang."""
    pipe = _pipe(limit=0)
    big = "word " * 2000  # 10k chars, always fails
    out = pipe._extract_by_splitting(big, category="general", metadata=None)
    assert out is None
    # depth<=2 and floor stop it well short of one call per word.
    assert len(pipe._graph_extraction.calls) < 20


def test_partial_recovery_keeps_the_half_that_worked():
    """If one half still fails past the depth bound, keep the half that parsed."""
    # left half ~900 (parses at limit 1000), right half ~3000 (fails, and its
    # sub-halves ~1500 also fail) -> keep the left.
    pipe = _pipe(limit=1000)
    text = ("Alpha " * 150).strip() + "\n\n" + ("Bravo " * 500).strip()
    out = pipe._extract_by_splitting(text, category="general", metadata=None)
    assert out is not None
    names = {n["properties"]["name"] for n in out["nodes"]}
    assert "Alpha" in names, "the half that parsed must be kept"


def test_pipeline_tries_split_before_heuristic():
    """The wiring: the split path runs before the capitalized-token fallback."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "src" / "seocho" / "index" / "pipeline.py").read_text()
    exc_block = src[src.index("except Exception as exc:"):]
    exc_block = exc_block[:exc_block.index("_fallback_extract")]
    assert "_extract_by_splitting" in exc_block, (
        "the heuristic fallback runs before the split retry is attempted"
    )
