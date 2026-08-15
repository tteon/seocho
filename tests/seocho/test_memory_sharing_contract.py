"""The cross-agent memory sharing contract, held as executable statements.

The contract (docs/MEMORY_SHARING.md): **conversations are private, knowledge
is shared.** Two agents pointed at the same backend and workspace read each
other's entities the moment a write returns; their session state never
leaks to each other; a different workspace sees nothing. These tests are the
contract's proof obligations — if one fails, the document is lying.
"""

from __future__ import annotations

import pytest

from seocho.agent.context import SessionContext
from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.store.graph import LadybugGraphStore


@pytest.fixture
def ontology() -> Ontology:
    return Ontology(
        name="shared_memory_contract",
        graph_model="lpg",
        nodes={
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={"WORKS_AT": RelDef(source="Person", target="Company")},
    )


@pytest.fixture
def backend(ontology, tmp_path):
    """One shared backend, as two cooperating agents would hold it."""
    store = LadybugGraphStore(str(tmp_path / "shared.lbug"))
    store.ensure_constraints(ontology)
    yield store
    store.close()


def _read_people(store, workspace_id: str) -> set:
    rows = store.query(
        "MATCH (p:Person) WHERE p._workspace_id = $workspace_id "
        "RETURN p.name AS name",
        params={"workspace_id": workspace_id},
    )
    return {row["name"] for row in rows}


class TestEntityNamespaceIsShared:
    def test_agent_b_reads_what_agent_a_wrote_as_soon_as_the_write_returns(
        self, backend
    ):
        # Agent A (say, a Python ingestion job) writes an entity …
        backend.write(
            [{"id": "alice", "label": "Person", "properties": {"name": "Alice"}}],
            [],
            workspace_id="acme",
            source_id="agent-a",
        )
        # … and Agent B (any other process on the same backend) sees it with
        # no copy step, no message bus, no cache invalidation.
        assert _read_people(backend, "acme") == {"Alice"}

    def test_workspaces_are_fully_isolated_from_each_other(self, backend):
        backend.write(
            [{"id": "alice", "label": "Person", "properties": {"name": "Alice"}}],
            [],
            workspace_id="acme",
            source_id="agent-a",
        )
        backend.write(
            [{"id": "bob", "label": "Person", "properties": {"name": "Bob"}}],
            [],
            workspace_id="globex",
            source_id="agent-c",
        )
        assert _read_people(backend, "acme") == {"Alice"}
        assert _read_people(backend, "globex") == {"Bob"}

    def test_unscoped_reads_are_refused_when_enforcement_is_on(self, backend):
        """With ``enforce_workspace_filter=True`` (the multi-tenant
        deployment posture; the query proxy applies it at the runtime
        boundary), a query that forgets the workspace filter is an error,
        not a cross-tenant read — fail-closed, opt-in per the contract."""
        from seocho.store.graph import WorkspaceFilterMissingError

        with pytest.raises(WorkspaceFilterMissingError):
            backend.query("MATCH (p:Person) RETURN p.name AS name",
                          enforce_workspace_filter=True)


class TestConversationNamespaceIsPrivate:
    def test_session_context_never_bleeds_between_sessions(self):
        """Each agent's conversational working set lives in its own
        SessionContext object; registering entities in one is invisible to
        the other even inside the same process."""
        context_a, context_b = SessionContext(), SessionContext()
        context_a.register_entities(
            [{"label": "Person", "properties": {"name": "Alice"}}],
            source_id="conv-a",
        )
        assert context_a.entities
        assert not context_b.entities
        assert not context_b.queries

    def test_sessions_get_distinct_identities(self, ontology, tmp_path):
        """Two Session objects over ONE shared store: distinct session_id
        (private namespace key), same graph_store (shared namespace)."""
        from seocho.session import Session

        store = LadybugGraphStore(str(tmp_path / "sessions.lbug"))
        store.ensure_constraints(ontology)
        try:
            session_a = Session(ontology=ontology, graph_store=store,
                                llm=None, workspace_id="acme")
            session_b = Session(ontology=ontology, graph_store=store,
                                llm=None, workspace_id="acme")
            assert session_a.session_id != session_b.session_id
            assert session_a.graph_store is session_b.graph_store
            assert session_a.context is not session_b.context
        finally:
            store.close()


class TestSubScopingSeam:
    def test_user_identity_lives_on_the_session_not_in_the_graph(self, ontology):
        """Deliberate divergence from the NAMS pattern (hadry, 2026-08-15):
        user_id is NOT stamped onto graph nodes. The data plane scopes by
        workspace_id only; user-level identity rides the session object and
        the trace/log plane. ACL-filtered answering is seocho-vdw.7 (H4) and
        filters at answer time, not by node properties."""
        from seocho.session import Session

        session = Session(ontology=ontology, graph_store=None, llm=None,
                          workspace_id="acme", user_id="user-42")
        assert session.user_id == "user-42"
        # The graph write path takes workspace_id and source_id; there is no
        # user_id parameter to smuggle identity into node properties with.
        import inspect

        from seocho.store.graph import LadybugGraphStore

        write_params = inspect.signature(LadybugGraphStore.write).parameters
        assert "workspace_id" in write_params
        assert "user_id" not in write_params
