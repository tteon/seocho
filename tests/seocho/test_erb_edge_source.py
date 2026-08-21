"""The ERB graph arm must be built from our own extraction, not the answer key.

The project's stated decision, recorded in `scripts/serve_track/erb_index.py`,
is that the graph arm must come from SEOCHO's own indexing: *"That version built
the graph arm from the dataset's `answer_facts` ... it routes around the thing
this project actually builds."* The adapter did it anyway, and was modified later
than that decision.

What `answer_facts` actually contains, from the committed run:

    (The answer must state that the updated reservation target)
      -[IS]-> (30% of interactive burst credits ... on dp-132-usw)
    (The answer) -[MUST]-> (not claim the reservation target is 20% ...)

That is the marking scheme converted to pseudo-triples. The graph arm was
reading the rubric it would be graded against — and `ERB_conflicting_info`, the
stratum where those edges are densest, is the *only* statistically significant
graph-vs-vector result in the whole programme.

`answer_facts` stays reachable, because an oracle arm is a legitimate ceiling.
It is not the default, and its label says what it is.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "scripts" / "serve_track" / "erb_adapter.py"


@pytest.fixture(scope="module")
def adapter():
    spec = importlib.util.spec_from_file_location("erb_adapter", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_extraction(tmp_path: Path) -> Path:
    """One document's extracted graph, in erb_index.py's output shape."""
    path = tmp_path / "extracted.jsonl"
    path.write_text(json.dumps({
        "doc_id": "doc-1",
        "nodes": [
            {"id": "org_1", "label": "Org", "properties": {"name": "Redwood"}},
            {"id": "dec_1", "label": "Decision", "properties": {"name": "Retry v1"}},
        ],
        "relationships": [
            {"source": "org_1", "target": "dec_1", "type": "DECIDED"},
        ],
    }) + "\n", encoding="utf-8")
    return path


def test_extraction_loads_as_named_triples(adapter, tmp_path):
    graphs = adapter.load_extracted_graph(_write_extraction(tmp_path))
    assert graphs == {"doc-1": [("Redwood", "DECIDED", "Retry v1")]}, (
        "node ids must resolve to names; document-local ids are meaningless "
        "in a context string"
    )


def test_failed_extractions_are_skipped(adapter, tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text(json.dumps({
        "doc_id": "doc-err", "error": "timeout", "nodes": [], "relationships": [],
    }) + "\n", encoding="utf-8")
    assert adapter.load_extracted_graph(path) == {}


def test_graph_arm_defaults_to_our_extraction(adapter, tmp_path):
    row = {
        "question_id": "q1", "question": "who decided?",
        "gold_answer": "Redwood", "question_type": "single_hop",
        "expected_doc_ids": ["doc-1"],
        "answer_facts": ["The answer must state that Redwood decided"],
    }
    item = adapter.build_item(
        row, {"doc-1": "Redwood decided Retry v1."},
        extracted_graph=adapter.load_extracted_graph(_write_extraction(tmp_path)),
        edge_source_mode="extracted",
        max_doc_chars=6000, neighbourhood=3, from_parquet=True,
    )

    assert item["strata"]["edge_source"] == "extracted"
    assert ("Redwood", "DECIDED", "Retry v1") in [tuple(e) for e in item["gold_edges"]]
    flattened = " ".join(" ".join(e) for e in item["gold_edges"])
    assert "must state" not in flattened, "a rubric line leaked into the graph arm"


def test_answer_facts_is_reachable_but_labelled_as_a_ceiling(adapter, tmp_path):
    """An oracle arm is legitimate. Presenting it as a graph result is not."""
    row = {
        "question_id": "q1", "question": "who decided?",
        "gold_answer": "Redwood", "question_type": "single_hop",
        "expected_doc_ids": ["doc-1"],
        "answer_facts": ["The answer must state that Redwood decided"],
    }
    item = adapter.build_item(
        row, {"doc-1": "Redwood decided Retry v1."},
        extracted_graph={}, edge_source_mode="answer_facts",
        max_doc_chars=6000, neighbourhood=3, from_parquet=True,
    )

    assert "CEILING" in item["strata"]["edge_source"], (
        "the oracle arm must announce itself, or it will be read as a graph win"
    )


def test_cli_default_is_the_extracted_graph():
    source = ADAPTER.read_text()
    assert '"--edge-source"' in source
    assert 'default="extracted"' in source, (
        "answer_facts must be opt-in; it was the default and produced the "
        "programme's only significant result"
    )
