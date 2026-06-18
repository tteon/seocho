"""Regression for #133 — content dedup must be scoped to a single batch ingest,
not a process-lifetime instance set. The shared `_seen_hashes` set silently
dropped a document re-submitted in a separate add()/index() call.
"""

from __future__ import annotations

from seocho.index.pipeline import IndexingPipeline
from seocho.indexing import IndexingResult, content_hash
from seocho.ontology import NodeDef, Ontology, P


def _pipeline() -> IndexingPipeline:
    onto = Ontology(name="t", nodes={"Doc": NodeDef(properties={"name": P(str)})})
    # graph_store/llm are unused on the paths under test.
    return IndexingPipeline(ontology=onto, graph_store=object(), llm=object())


def test_no_process_lifetime_dedup_state() -> None:
    # The cross-call leak was the instance set itself; it must be gone.
    assert not hasattr(_pipeline(), "_seen_hashes")


def test_duplicate_within_batch_set_is_skipped_early() -> None:
    # Second occurrence within one batch (its hash already in the batch set)
    # short-circuits to deduplicated without touching the graph/llm.
    pipeline = _pipeline()
    text = "hello world"
    result = pipeline.index(text, _seen_hashes={content_hash(text)})
    assert result.deduplicated is True
    assert result.skipped_chunks == 1


def test_standalone_index_has_no_dedup_set() -> None:
    # No _seen_hashes passed -> dedup gate is never consulted, so a standalone
    # re-submission is not dropped. The duplicate branch needs a set to fire;
    # with None it cannot, which is the fix.
    pipeline = _pipeline()
    # Pre-"seeing" the hash is impossible because there is no shared set; assert
    # the signature default keeps standalone calls independent.
    import inspect

    assert inspect.signature(pipeline.index).parameters["_seen_hashes"].default is None


def test_each_batch_gets_a_fresh_dedup_set(monkeypatch) -> None:
    pipeline = _pipeline()
    seen_sets = []

    def fake_index(doc, *, _seen_hashes=None, **kwargs):
        seen_sets.append(_seen_hashes)
        return IndexingResult(source_id="x", chunks_processed=1, total_nodes=0)

    monkeypatch.setattr(pipeline, "index", fake_index)
    pipeline.index_batch(["a", "a"])  # two docs, one batch
    pipeline.index_batch(["a"])       # a separate batch

    assert seen_sets[0] is seen_sets[1]        # same batch -> same dedup set
    assert seen_sets[0] is not seen_sets[2]    # new batch -> fresh set
    assert isinstance(seen_sets[0], set)
