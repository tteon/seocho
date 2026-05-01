"""Regression tests for user_id / workspace_id propagation into trace metadata.

CLAUDE.md §9 declares that trace metadata should carry workspace and user
context. Before this change, ``Session`` accepted neither — so multi-user
projects could not distinguish whose query produced a given span. The
``Seocho`` client already had ``user_id`` on its constructor; we now
forward it into ``Session`` and stamp it (alongside ``workspace_id``) on
every emitted span's ``metadata=`` dict.

These tests pin the wire-through so that:

1. ``Session(user_id=...)`` captures the value on the instance.
2. ``Seocho(user_id=...).session(...)`` propagates it to the Session.
3. The emitted span metadata carries ``user_id`` and ``workspace_id``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _make_minimal_ontology() -> Any:
    """Smallest valid ontology so Session.__init__ doesn't reject inputs."""
    from seocho import NodeDef, Ontology, P

    return Ontology(
        name="userid_test",
        nodes={"Person": NodeDef(properties={"name": P(str, unique=True)})},
    )


class _RecordingTrace:
    """Captures every span emit so tests can assert on metadata."""

    def __init__(self) -> None:
        self.session_id = "test-trace"
        self.name = "test"
        self.spans: List[Dict[str, Any]] = []

    def log_span(
        self,
        name: str,
        *,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        self.spans.append(
            {
                "name": name,
                "input_data": input_data or {},
                "output_data": output_data or {},
                "metadata": metadata or {},
                "tags": tags or [],
            }
        )

    def end(self) -> Dict[str, Any]:
        return {"session_id": self.session_id, "total_spans": len(self.spans)}


def test_session_captures_user_id() -> None:
    """Direct constructor: user_id is stored on the instance."""
    from seocho.session import Session

    s = Session(
        ontology=_make_minimal_ontology(),
        graph_store=object(),
        llm=object(),
        user_id="alice@team",
        workspace_id="acme",
    )
    assert s.user_id == "alice@team"
    assert s.workspace_id == "acme"


def test_session_user_id_defaults_to_none() -> None:
    """Backwards compatible: omitting user_id leaves it None, not ''."""
    from seocho.session import Session

    s = Session(
        ontology=_make_minimal_ontology(),
        graph_store=object(),
        llm=object(),
    )
    assert s.user_id is None


def test_seocho_client_forwards_user_id_to_session() -> None:
    """``Seocho(user_id=...).session(...)`` plumbs user_id all the way through."""
    from seocho import Seocho
    from seocho.store.graph import LadybugGraphStore
    import tempfile

    onto = _make_minimal_ontology()
    with tempfile.TemporaryDirectory() as tmp:
        store = LadybugGraphStore(f"{tmp}/forward.lbug")
        client = Seocho(
            ontology=onto,
            graph_store=store,
            llm=object(),
            user_id="bob@team",
            workspace_id="acme",
        )
        sess = client.session("forward-demo")
        assert sess.user_id == "bob@team"
        assert sess.workspace_id == "acme"


def test_span_metadata_contains_user_id_and_workspace_id() -> None:
    """add() emits a span whose metadata carries both identity fields."""
    from seocho.agent.context import SessionContext
    from seocho.session import Session

    sess = Session(
        ontology=_make_minimal_ontology(),
        graph_store=object(),
        llm=object(),
        user_id="carol@team",
        workspace_id="acme",
    )
    # Substitute the recording trace and stub the pipeline path so we don't
    # need a real graph store. _add_via_pipeline is the deterministic path
    # that runs in execution_mode='pipeline' (the default).
    recording = _RecordingTrace()
    sess._trace = recording
    sess.context = SessionContext()

    def _stub_pipeline(content, database, category, metadata):
        return {
            "extracted_nodes": [],
            "extracted_relationships": [],
            "nodes_created": 0,
            "relationships_created": 0,
            "mode": "pipeline",
        }

    sess._add_via_pipeline = _stub_pipeline  # type: ignore[method-assign]

    sess.add("Tim Cook leads Apple.")

    assert len(recording.spans) == 1
    md = recording.spans[0]["metadata"]
    assert md["user_id"] == "carol@team"
    assert md["workspace_id"] == "acme"
