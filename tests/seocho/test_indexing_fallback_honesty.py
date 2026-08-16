"""A degraded extraction must not report itself as a clean success.

`IndexingResult.ok` used to be `no write errors and chunks_processed > 0`. Under
strict enforcement that was already right: an extraction failure lands in
`write_errors`, so `ok` was False. Under the default guided/open profiles it was
not. There, `allow_heuristic_fallback` is True, so a failure substitutes the
capitalized-token heuristic — manufactured `Entity`/`MENTIONS` structure that
the ontology never declared — records no error, and `ok` stayed True.

The degradation was reported, in `fallback_used`. That only helps a caller who
already suspects it, and the callers that matter (batch counters, CI gates) read
`ok`. So a run could substitute ontology-free structure for every chunk and be
counted a success.
"""

from __future__ import annotations

from seocho.index.pipeline import BatchIndexingResult, IndexingResult


def test_clean_run_is_ok():
    result = IndexingResult(chunks_processed=3)
    assert result.ok


def test_fallback_run_is_not_ok():
    result = IndexingResult(chunks_processed=3, fallback_used=True,
                            fallback_reason="extraction timed out")
    assert not result.ok, (
        "a run whose graph came from the heuristic fallback reported success"
    )


def test_write_errors_still_dominate():
    """The original condition must keep working, not be replaced by the new one."""
    result = IndexingResult(chunks_processed=3, write_errors=["boom"])
    assert not result.ok

    assert not IndexingResult(chunks_processed=0).ok


def test_batch_counts_a_degraded_document_as_failed():
    """The reason `ok` matters: batch counters read it, `fallback_used` they do not."""
    batch = BatchIndexingResult(total_documents=2)
    for result in (IndexingResult(chunks_processed=1),
                   IndexingResult(chunks_processed=1, fallback_used=True)):
        batch.results.append(result)
        if result.deduplicated:
            batch.skipped += 1
        elif result.ok:
            batch.successful += 1
        else:
            batch.failed += 1

    assert (batch.successful, batch.failed) == (1, 1)
    assert not batch.ok


def test_ok_is_surfaced_in_to_dict():
    """Consumers reading the serialized form must see the same verdict."""
    payload = IndexingResult(chunks_processed=1, fallback_used=True).to_dict()
    assert payload["ok"] is False
    assert payload["fallback_used"] is True
