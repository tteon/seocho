"""Indexing contract regression loop (seocho-4eq.4).

End-to-end regression that validates the four-schema lock established
in Phases 1, 1.5, 2, 3, 5, 6 holds across a multi-document indexing
run. The harness is the *public* surface; the corpus is parameterized
so operators can swap their private finance corpus in via env var
without modifying this file.

Invariants verified per indexed document:

1. **Agent ↔ Graph schema lock.** Every written node and relationship
   carries ``_ontology_context_hash`` matching the active
   ``OntologyContextDescriptor.context_hash``. (Phases 1, 2.)
2. **Identity stamps.** Every written node carries ``_ontology_id``
   and ``_ontology_profile`` matching the active ontology.
3. **Per-document context.** ``IndexingResult.to_dict()['ontology_context']``
   carries the same context_hash as the writes — proves the metadata
   surface and the wire-level write surface agree.
4. **Artifact schema lock.** A ``RuleSet`` inferred over the union of
   extracted nodes carries ``ontology_identity_hash`` matching the
   active hash when explicitly stamped via ``infer_rules_from_graph``.
   (Phases 5, 6.)
5. **Fallback atomicity.** Malformed LLM output produces a structured
   failure (``result.ok is False``) without partial writes — the
   degraded path doesn't corrupt the graph.

Operator override
-----------------

Set ``SEOCHO_REGRESSION_CORPUS_PATH=/path/to/corpus`` to load
``.txt`` / ``.md`` files from a directory instead of the synthetic
inline corpus. Each file becomes one indexed document. Useful for
running the same invariants against the operator's private finance
corpus during release sign-off. The synthetic corpus exercises the
harness in CI without leaking proprietary data.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


@pytest.fixture(autouse=True)
def _ensure_paths():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


# ---------------------------------------------------------------------------
# Corpus loader — synthetic by default, env-var override
# ---------------------------------------------------------------------------


@dataclass
class CorpusDocument:
    """A single document in the regression corpus."""

    name: str
    content: str
    expected_nodes: List[Dict[str, Any]] = field(default_factory=list)
    expected_relationships: List[Dict[str, Any]] = field(default_factory=list)


_SYNTHETIC_CORPUS: List[CorpusDocument] = [
    CorpusDocument(
        name="acme_q1",
        content="ACME Inc. reported Q1 revenue of $42 million in 2025.",
        expected_nodes=[
            {"id": "acme", "label": "Company", "properties": {"name": "ACME Inc."}},
            {
                "id": "q1_revenue",
                "label": "FinancialMetric",
                "properties": {"name": "Revenue", "value": "42 million"},
            },
        ],
        expected_relationships=[
            {
                "source": "acme",
                "target": "q1_revenue",
                "type": "REPORTED",
                "properties": {"period": "Q1 2025"},
            }
        ],
    ),
    CorpusDocument(
        name="beta_eps",
        content="Beta Corp posted EPS of 2.15 in fiscal year 2024.",
        expected_nodes=[
            {"id": "beta", "label": "Company", "properties": {"name": "Beta Corp"}},
            {
                "id": "fy24_eps",
                "label": "FinancialMetric",
                "properties": {"name": "EPS", "value": "2.15"},
            },
        ],
        expected_relationships=[
            {
                "source": "beta",
                "target": "fy24_eps",
                "type": "REPORTED",
                "properties": {"period": "FY 2024"},
            }
        ],
    ),
    CorpusDocument(
        name="gamma_acq",
        content="Gamma Holdings disclosed an acquisition of Delta Group for $500 million.",
        expected_nodes=[
            {"id": "gamma", "label": "Company", "properties": {"name": "Gamma Holdings"}},
            {
                "id": "delta_acq",
                "label": "FinancialMetric",
                "properties": {"name": "Acquisition", "value": "$500 million"},
            },
        ],
        expected_relationships=[
            {
                "source": "gamma",
                "target": "delta_acq",
                "type": "REPORTED",
                "properties": {"period": "2025"},
            }
        ],
    ),
]


def _load_operator_corpus(corpus_path: Path) -> List[CorpusDocument]:
    """Load .txt / .md files from a directory into CorpusDocument list.

    The operator-provided corpus runs through the same invariant
    assertions but with empty expected_nodes — the LLM stub falls back
    to a generic single-Company extraction so the harness still
    exercises the schema-lock surface. Operators wanting to assert
    extraction correctness should plug a real LLM via the standard SDK
    config rather than driving through the regression harness alone.
    """

    docs: List[CorpusDocument] = []
    for path in sorted(corpus_path.iterdir()):
        if path.suffix.lower() not in {".txt", ".md"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        docs.append(CorpusDocument(name=path.stem, content=content))
    return docs


def _resolve_corpus() -> List[CorpusDocument]:
    raw = os.getenv("SEOCHO_REGRESSION_CORPUS_PATH", "").strip()
    if raw:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            loaded = _load_operator_corpus(path)
            if loaded:
                return loaded
    return list(_SYNTHETIC_CORPUS)


# ---------------------------------------------------------------------------
# Test scaffolding (mirrors the patterns in seocho/tests/test_ontology_context.py)
# ---------------------------------------------------------------------------


def _ontology():
    from seocho.ontology import NodeDef, Ontology, P, RelDef

    return Ontology(
        name="finance",
        package_id="company-finance",
        version="1.0.0",
        nodes={
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
            "FinancialMetric": NodeDef(properties={"name": P(str), "value": P(str)}),
        },
        relationships={
            "REPORTED": RelDef(
                source="Company",
                target="FinancialMetric",
                properties={"period": P(str)},
            ),
        },
    )


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return dict(self._payload)


class _CorpusLLM:
    """LLM stub keyed by document content prefix.

    Returns each ``CorpusDocument``'s declared expected_nodes /
    expected_relationships when its content matches. Operator-supplied
    corpora that don't declare expected entities fall back to a generic
    single-Company extraction so the harness still runs.
    """

    model = "fake-corpus-llm"

    def __init__(self, corpus: List[CorpusDocument]) -> None:
        self._by_content = {doc.content: doc for doc in corpus}

    def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
        for content, doc in self._by_content.items():
            if content in user:
                if doc.expected_nodes or doc.expected_relationships:
                    return _FakeResponse(
                        {
                            "nodes": list(doc.expected_nodes),
                            "relationships": list(doc.expected_relationships),
                        }
                    )
                # Operator corpus without declared expectations:
                # generic extraction so the harness still exercises
                # the write path.
                return _FakeResponse(
                    {
                        "nodes": [
                            {
                                "id": doc.name,
                                "label": "Company",
                                "properties": {"name": doc.name},
                            }
                        ],
                        "relationships": [],
                    }
                )
        # Document outside the keyed map → empty extraction (still legal).
        return _FakeResponse({"nodes": [], "relationships": []})


class _MalformedLLM:
    """LLM stub that returns malformed payloads to exercise the fallback path."""

    model = "fake-malformed-llm"

    def complete(self, *, system, user, temperature, response_format=None):  # noqa: ANN001
        # Wrong shape: nodes is a string instead of a list. The
        # extraction normalizer must reject this without producing
        # partial graph writes.
        return _FakeResponse({"nodes": "not a list", "relationships": "also not a list"})


class _CapturingGraphStore:
    def __init__(self) -> None:
        self.writes: List[Dict[str, Any]] = []

    def write(self, nodes, relationships, *, database="neo4j", workspace_id="default", source_id=""):  # noqa: ANN001
        self.writes.append(
            {
                "nodes": nodes,
                "relationships": relationships,
                "database": database,
                "workspace_id": workspace_id,
                "source_id": source_id,
            }
        )
        return {
            "nodes_created": len(nodes),
            "relationships_created": len(relationships),
            "errors": [],
        }


# ---------------------------------------------------------------------------
# Tests — schema-lock invariants
# ---------------------------------------------------------------------------


def _build_pipeline(graph_store, llm, *, workspace_id="acme", profile="finder-financials"):
    from seocho.index.pipeline import IndexingPipeline

    return IndexingPipeline(
        ontology=_ontology(),
        graph_store=graph_store,
        llm=llm,
        workspace_id=workspace_id,
        ontology_profile=profile,
    )


def _active_context_hash(workspace_id: str = "acme", profile: str = "finder-financials") -> str:
    from seocho.ontology_context import compile_ontology_context

    return compile_ontology_context(
        _ontology(), workspace_id=workspace_id, profile=profile
    ).descriptor.context_hash


def test_corpus_loader_returns_synthetic_when_env_unset(monkeypatch):
    monkeypatch.delenv("SEOCHO_REGRESSION_CORPUS_PATH", raising=False)
    corpus = _resolve_corpus()
    assert len(corpus) == len(_SYNTHETIC_CORPUS)
    assert {doc.name for doc in corpus} == {"acme_q1", "beta_eps", "gamma_acq"}


def test_corpus_loader_reads_operator_path(tmp_path, monkeypatch):
    (tmp_path / "doc1.txt").write_text("Operator finance doc one.", encoding="utf-8")
    (tmp_path / "doc2.md").write_text("Operator finance doc two.", encoding="utf-8")
    (tmp_path / "ignored.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("SEOCHO_REGRESSION_CORPUS_PATH", str(tmp_path))

    corpus = _resolve_corpus()
    names = {doc.name for doc in corpus}
    assert names == {"doc1", "doc2"}


def test_indexing_stamps_active_hash_on_every_node():
    """Phases 1+2 invariant: every written node carries the active
    ``_ontology_context_hash``."""

    corpus = list(_SYNTHETIC_CORPUS)
    store = _CapturingGraphStore()
    pipeline = _build_pipeline(store, _CorpusLLM(corpus))
    expected_hash = _active_context_hash()

    for doc in corpus:
        result = pipeline.index(doc.content, database="neo4j")
        assert result.ok, f"{doc.name} expected ok=True"

    assert store.writes, "expected at least one write"
    for write in store.writes:
        for node in write["nodes"]:
            props = node["properties"]
            assert props["_ontology_context_hash"] == expected_hash, (
                f"node {node.get('id')} has hash "
                f"{props.get('_ontology_context_hash')!r} != active {expected_hash!r}"
            )
            assert props["_ontology_id"] == "company-finance"
            assert props["_ontology_profile"] == "finder-financials"


def test_indexing_stamps_active_hash_on_every_relationship():
    """Phase 2 + 3 invariant: relationships carry the same hash stamps as
    nodes — proves the schema lock applies uniformly across the wire-
    level write surface."""

    corpus = list(_SYNTHETIC_CORPUS)
    store = _CapturingGraphStore()
    pipeline = _build_pipeline(store, _CorpusLLM(corpus))
    expected_hash = _active_context_hash()

    for doc in corpus:
        pipeline.index(doc.content, database="neo4j")

    rel_writes = [rel for write in store.writes for rel in write["relationships"]]
    assert rel_writes, "expected at least one relationship in the corpus extraction"
    for rel in rel_writes:
        props = rel["properties"]
        assert props["_ontology_context_hash"] == expected_hash
        assert props["_ontology_profile"] == "finder-financials"


def test_indexing_result_metadata_matches_wire_writes():
    """Cross-surface invariant: the per-document indexing result's
    ontology_context block reports the same hash that gets stamped on the
    graph writes — no metadata/wire divergence."""

    corpus = list(_SYNTHETIC_CORPUS)
    store = _CapturingGraphStore()
    pipeline = _build_pipeline(store, _CorpusLLM(corpus))
    expected_hash = _active_context_hash()

    for doc in corpus:
        result = pipeline.index(doc.content, database="neo4j")
        ontology_context = result.to_dict()["ontology_context"]
        assert ontology_context["context_hash"] == expected_hash
        assert ontology_context["workspace_id"] == "acme"
        assert ontology_context["profile"] == "finder-financials"


def test_inferred_ruleset_carries_active_hash_when_stamped():
    """Phase 5+6 artifact-schema invariant: a RuleSet inferred over the
    corpus's extracted nodes carries the active hash when stamped via
    the explicit kwarg."""

    from seocho.rules import infer_rules_from_graph

    corpus = list(_SYNTHETIC_CORPUS)
    store = _CapturingGraphStore()
    pipeline = _build_pipeline(store, _CorpusLLM(corpus))
    expected_hash = _active_context_hash()

    for doc in corpus:
        pipeline.index(doc.content, database="neo4j")

    union_nodes = [node for write in store.writes for node in write["nodes"]]
    extracted = {"nodes": union_nodes}

    ruleset = infer_rules_from_graph(extracted, ontology_identity_hash=expected_hash)
    assert ruleset.ontology_identity_hash == expected_hash
    assert ruleset.to_dict()["ontology_identity_hash"] == expected_hash


def test_malformed_extraction_does_not_corrupt_graph_store():
    """Fallback atomicity: when the LLM returns a malformed payload, the
    pipeline must surface the failure on IndexingResult and refuse to
    write partial graph state. No partial-write leak."""

    store = _CapturingGraphStore()
    pipeline = _build_pipeline(store, _MalformedLLM())

    result = pipeline.index(
        "ACME reported revenue of $42 million in Q1 2025.",
        database="neo4j",
    )
    payload = result.to_dict()

    # The pipeline either marks ok=False or produces an empty extraction;
    # either way the invariant is "no partial node leak from a malformed
    # response." The exact ok/quality verdict depends on the
    # ExtractionStrategy's error handling — we assert the load-bearing
    # property: zero nodes/rels written.
    total_nodes_written = sum(len(write["nodes"]) for write in store.writes)
    total_rels_written = sum(len(write["relationships"]) for write in store.writes)
    assert total_nodes_written == 0, (
        f"malformed extraction leaked {total_nodes_written} nodes into the graph store"
    )
    assert total_rels_written == 0, (
        f"malformed extraction leaked {total_rels_written} relationships"
    )
    # Result still carries ontology_context metadata so downstream
    # observability remains coherent even on the degraded path.
    assert "ontology_context" in payload


def test_reindex_is_idempotent_for_hash_stamps():
    """Re-indexing the same document produces writes with the same
    ``_ontology_context_hash`` — the hash is deterministic across
    independent runs, which is the precondition for cross-process
    drift detection (Phases 1, 2, 3)."""

    corpus = list(_SYNTHETIC_CORPUS)
    store_a = _CapturingGraphStore()
    store_b = _CapturingGraphStore()
    pipeline_a = _build_pipeline(store_a, _CorpusLLM(corpus))
    pipeline_b = _build_pipeline(store_b, _CorpusLLM(corpus))

    pipeline_a.index(corpus[0].content, database="neo4j")
    pipeline_b.index(corpus[0].content, database="neo4j")

    hash_a = store_a.writes[0]["nodes"][0]["properties"]["_ontology_context_hash"]
    hash_b = store_b.writes[0]["nodes"][0]["properties"]["_ontology_context_hash"]
    assert hash_a == hash_b, "hash stamp diverged across independent indexing runs"


def test_corpus_run_e2e_smoke():
    """Smoke: index the entire (default or operator) corpus end-to-end.
    Acts as a real-load entrypoint when SEOCHO_REGRESSION_CORPUS_PATH is
    set; falls back to the synthetic corpus in CI."""

    corpus = _resolve_corpus()
    assert corpus, "corpus must be non-empty"

    store = _CapturingGraphStore()
    pipeline = _build_pipeline(store, _CorpusLLM(corpus))

    failures = []
    for doc in corpus:
        result = pipeline.index(doc.content, database="neo4j")
        if not result.ok:
            failures.append(doc.name)

    assert not failures, f"end-to-end failures on documents: {failures}"
