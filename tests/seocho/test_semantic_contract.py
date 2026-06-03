"""Writer↔reader coherence contract (ADR-0103 S7) — the anti-drift lock.

The whole redesign rests on extraction (writer) and query (reader) deriving the
SAME deterministic Observation identity from the same shared functions. This
test fails the moment they drift:

1. round-trip: the obs_id the WRITER stamps on a fact == the obs_id the READER
   derives from the surface question for that same fact (same workspace);
2. single source of truth: writer and reader import the same `observation_key`
   function object (no shadow copy);
3. closed-set guard: any concept the reader resolves is a ConceptRegistry member.

No graph, no LLM.
"""

from __future__ import annotations

import seocho.index.observation_writer as writer_mod
import seocho.semantic_layer.slots as slots_mod
from seocho.index.observation_writer import build_observations
from seocho.query.semantic_decompose import QuerySlots, resolve_slots
from seocho.semantic_layer import default_registry, default_resolver
from seocho.semantic_layer.keys import observation_key as canonical_key

WS = "contract-ws"


def _writer_obs_id(reg, res):
    """Run the WRITER path for 'Apple total revenue FY2024' → its obs_id."""
    nodes = [
        {"id": "apple", "label": "Company", "properties": {"name": "Apple Inc."}},
        {"id": "rev", "label": "Revenue",
         "properties": {"name": "Total Revenue FY2024", "value": "391035000000",
                        "period": "FY2024"}},
    ]
    rels = [{"source": "apple", "target": "rev", "type": "REPORTED"}]
    obs_nodes, _ = build_observations(nodes, rels, registry=reg, resolver=res,
                                      workspace_id=WS)
    obs = next(n for n in obs_nodes if n["label"] == "Observation")
    return obs["properties"]["obs_id"]


def _reader_obs_id(reg, res):
    """Run the READER path for the same fact, from the surface question slots."""
    qs = QuerySlots("metric_lookup", "total revenue", "Apple Inc.", "FY2024")
    slots = resolve_slots(qs, registry=reg, resolver=res)
    assert slots.is_fully_resolved
    return slots.observation_keys(workspace_id=WS)[0]


def test_writer_and_reader_derive_identical_obs_id():
    reg, res = default_registry(), default_resolver()
    assert _writer_obs_id(reg, res) == _reader_obs_id(reg, res)


def test_workspace_isolation_changes_the_key():
    reg, res = default_registry(), default_resolver()
    qs = QuerySlots("metric_lookup", "total revenue", "Apple Inc.", "FY2024")
    s = resolve_slots(qs, registry=reg, resolver=res)
    assert s.observation_keys(workspace_id="ws-a")[0] != s.observation_keys(workspace_id="ws-b")[0]


def test_single_source_of_truth_same_function_object():
    # writer, slots, and the canonical module must be the SAME function object
    assert writer_mod.observation_key is canonical_key
    assert slots_mod.observation_key is canonical_key


def test_reader_resolved_concept_is_a_registry_member():
    reg, res = default_registry(), default_resolver()
    for surface in ("total revenue", "net income", "net sales", "earnings"):
        qs = QuerySlots("metric_lookup", surface, "Apple Inc.", "FY2024")
        slots = resolve_slots(qs, registry=reg, resolver=res)
        assert reg.is_member(slots.concept_id), surface
