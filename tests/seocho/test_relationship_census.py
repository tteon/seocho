"""Per-stage relationship-survival census: the instrument that pinned the drop.

The census exists so a single live index run shows, stage by stage, how many
domain (non-provenance) relationships survive the write path -- rather than
inferring a loss from a "47 extracted, 0 persisted" end state. These tests lock
its two counting rules deterministically:

  * _relcensus counts only NON-provenance edges (a graph full of MENTIONS must
    not read as healthy domain structure), and records per stage.
  * _resolvable_rels counts only edges whose BOTH endpoints are real node ids --
    the exact predicate that went 12 -> 0 and located the orphaned-endpoint bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from seocho.index.pipeline import IndexingPipeline


@dataclass
class _Result:
    relationship_census: Dict[str, int] = field(default_factory=dict)


def _pipe() -> IndexingPipeline:
    return IndexingPipeline(ontology=object(), graph_store=object(), llm=object())


def test_census_counts_only_domain_edges():
    rels = [
        {"type": "RELATED_TO"},
        {"type": "MENTIONS"},       # provenance -- must not count
        {"type": "HAS_CHUNK"},      # provenance -- must not count
        {"type": "RELATED_TO"},
    ]
    r = _Result()
    _pipe()._relcensus(r, "extracted", rels)
    assert r.relationship_census["extracted"] == 2


def test_census_already_domain_counts_all():
    """After the store returns domain edges, they are all domain -- count raw."""
    r = _Result()
    _pipe()._relcensus(r, "written_to_store", [{}, {}, {}], already_domain=True)
    assert r.relationship_census["written_to_store"] == 3


def test_resolvable_rels_requires_both_endpoints():
    nodes = [{"id": "cornwall"}, {"id": "goonhilly_down"}]
    rels = [
        {"source": "goonhilly_down", "target": "cornwall"},  # both real -> kept
        {"source": "2", "target": "1"},                      # orphaned -> dropped
        {"source": "cornwall", "target": "99"},              # half real -> dropped
    ]
    out = IndexingPipeline._resolvable_rels(nodes, rels)
    assert len(out) == 1
    assert out[0]["source"] == "goonhilly_down"


def test_pipeline_wires_the_census_at_the_endpoint_stage():
    """The wiring: endpoints_resolvable must be measured, or the bug is invisible."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "src" / "seocho" / "index" / "pipeline.py").read_text()
    assert '"endpoints_resolvable"' in src, (
        "the endpoint-resolution stage must be censused -- it is where the drop was"
    )
