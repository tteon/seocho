"""Tests for Tier 1 UX convenience factories: ``Seocho.local``, ``Seocho.remote``,
and ``Seocho.agent(kind)``."""

from unittest.mock import MagicMock, patch

import pytest

from seocho.ontology import NodeDef, Ontology, P, RelDef


@pytest.fixture
def simple_ontology():
    return Ontology(
        name="test",
        nodes={
            "Person": NodeDef(properties={"name": P(str, unique=True)}),
            "Company": NodeDef(properties={"name": P(str, unique=True)}),
        },
        relationships={
            "WORKS_AT": RelDef(source="Person", target="Company"),
        },
    )


@pytest.fixture(autouse=True)
def _strip_fake_neo4j():
    """Remove fake ``neo4j`` modules other test files inject into ``sys.modules``.

    Other extraction tests use ``importlib.reload`` together with a fake
    ``neo4j`` module to test runtime_ingest without the real driver. That
    leaves a synthetic module behind that conflicts with our mocking.
    """
    import sys

    for mod_name in list(sys.modules):
        if mod_name == "neo4j" or mod_name.startswith("neo4j."):
            mod = sys.modules[mod_name]
            if not hasattr(mod, "__file__") or mod.__file__ is None:
                del sys.modules[mod_name]
    yield


class TestSeochoLocal:
    def test_local_default_provider(self, simple_ontology):
        """Seocho.local(ontology) uses openai/gpt-4o by default."""
        import seocho.store.graph as _graph_mod
        import seocho.store.llm as _llm_mod
        from seocho.client import Seocho

        with patch.object(_graph_mod, "Neo4jGraphStore") as mock_graph, patch.object(
            _llm_mod, "create_llm_backend"
        ) as mock_llm:
            mock_graph.return_value = MagicMock()
            mock_llm.return_value = MagicMock()

            s = Seocho.local(simple_ontology)

            mock_graph.assert_called_once_with(
                "bolt://localhost:7687", "neo4j", "password"
            )
            mock_llm.assert_called_once_with(
                provider="openai", model="gpt-4o", api_key=None
            )
            assert s._local_mode is True
            assert s.ontology is simple_ontology

    def test_local_custom_provider_slash_model(self, simple_ontology):
        """Seocho.local(llm='deepseek/deepseek-chat') parses provider/model."""
        import seocho.store.graph as _graph_mod
        import seocho.store.llm as _llm_mod
        from seocho.client import Seocho

        with patch.object(_graph_mod, "Neo4jGraphStore"), patch.object(
            _llm_mod, "create_llm_backend"
        ) as mock_llm:
            mock_llm.return_value = MagicMock()
            Seocho.local(simple_ontology, llm="deepseek/deepseek-chat")
            mock_llm.assert_called_once_with(
                provider="deepseek", model="deepseek-chat", api_key=None
            )

    def test_local_plain_model_defaults_to_openai(self, simple_ontology):
        """Seocho.local(llm='gpt-4o-mini') (no slash) defaults provider to openai."""
        import seocho.store.graph as _graph_mod
        import seocho.store.llm as _llm_mod
        from seocho.client import Seocho

        with patch.object(_graph_mod, "Neo4jGraphStore"), patch.object(
            _llm_mod, "create_llm_backend"
        ) as mock_llm:
            mock_llm.return_value = MagicMock()
            Seocho.local(simple_ontology, llm="gpt-4o-mini")
            mock_llm.assert_called_once_with(
                provider="openai", model="gpt-4o-mini", api_key=None
            )

    def test_local_custom_graph_uri(self, simple_ontology):
        """Seocho.local respects a custom Bolt URI and credentials."""
        import seocho.store.graph as _graph_mod
        import seocho.store.llm as _llm_mod
        from seocho.client import Seocho

        with patch.object(_graph_mod, "Neo4jGraphStore") as mock_graph, patch.object(
            _llm_mod, "create_llm_backend"
        ):
            Seocho.local(
                simple_ontology,
                graph="bolt://neo4j.internal:7687",
                neo4j_user="admin",
                neo4j_password="secret",
            )
            mock_graph.assert_called_once_with(
                "bolt://neo4j.internal:7687", "admin", "secret"
            )

    def test_local_forwards_kwargs(self, simple_ontology):
        """Seocho.local forwards extra kwargs to the constructor."""
        import seocho.store.graph as _graph_mod
        import seocho.store.llm as _llm_mod
        from seocho.client import Seocho

        with patch.object(_graph_mod, "Neo4jGraphStore"), patch.object(
            _llm_mod, "create_llm_backend"
        ):
            s = Seocho.local(simple_ontology, workspace_id="tenant-a")
            assert s.workspace_id == "tenant-a"


class TestSeochoRemote:
    def test_remote_builds_http_client(self):
        """Seocho.remote(url) creates an HTTP-mode client."""
        from seocho.client import Seocho

        s = Seocho.remote("http://api.example.com:8001")
        assert s._local_mode is False
        assert s.base_url.startswith("http://api.example.com:8001")


class TestSeochoAgent:
    def test_agent_requires_local_mode(self):
        """agent() raises RuntimeError when not in local engine mode."""
        from seocho.client import Seocho

        s = Seocho(base_url="http://localhost:8001")
        with pytest.raises(RuntimeError, match="local engine mode"):
            s.agent("indexing")

    def test_agent_unknown_kind(self, simple_ontology):
        """agent() raises ValueError for unknown kinds."""
        from seocho.client import Seocho

        import seocho.store.graph as _graph_mod
        import seocho.store.llm as _llm_mod

        with patch.object(_graph_mod, "Neo4jGraphStore"), patch.object(
            _llm_mod, "create_llm_backend"
        ):
            s = Seocho.local(simple_ontology)
            with pytest.raises(ValueError, match="Unknown agent kind"):
                s.agent("unknown")

    def test_agent_indexing_delegates_to_factory(self, simple_ontology):
        """agent('indexing') calls create_indexing_agent with pre-wired deps."""
        import seocho.agent.factory as _factory_mod
        import seocho.store.graph as _graph_mod
        import seocho.store.llm as _llm_mod
        from seocho.client import Seocho

        with patch.object(_graph_mod, "Neo4jGraphStore"), patch.object(
            _llm_mod, "create_llm_backend"
        ), patch.object(_factory_mod, "create_indexing_agent") as mock_factory:
            mock_factory.return_value = MagicMock(name="agent")
            s = Seocho.local(simple_ontology)
            agent = s.agent("indexing")

            mock_factory.assert_called_once()
            call_kwargs = mock_factory.call_args.kwargs
            assert call_kwargs["ontology"] is simple_ontology
            assert call_kwargs["graph_store"] is s.graph_store
            assert call_kwargs["llm"] is s.llm
            assert agent is mock_factory.return_value

    def test_agent_query_delegates_to_factory(self, simple_ontology):
        import seocho.agent.factory as _factory_mod
        import seocho.store.graph as _graph_mod
        import seocho.store.llm as _llm_mod
        from seocho.client import Seocho

        with patch.object(_graph_mod, "Neo4jGraphStore"), patch.object(
            _llm_mod, "create_llm_backend"
        ), patch.object(_factory_mod, "create_query_agent") as mock_factory:
            mock_factory.return_value = MagicMock()
            s = Seocho.local(simple_ontology)
            s.agent("query")
            mock_factory.assert_called_once()
            call_kwargs = mock_factory.call_args.kwargs
            assert call_kwargs["ontology_context"].descriptor.workspace_id == "default"
            assert call_kwargs["workspace_id"] == "default"

    def test_agent_supervisor_delegates_to_factory(self, simple_ontology):
        import seocho.agent.factory as _factory_mod
        import seocho.store.graph as _graph_mod
        import seocho.store.llm as _llm_mod
        from seocho.client import Seocho

        with patch.object(_graph_mod, "Neo4jGraphStore"), patch.object(
            _llm_mod, "create_llm_backend"
        ), patch.object(_factory_mod, "create_supervisor_agent") as mock_factory:
            mock_factory.return_value = MagicMock()
            s = Seocho.local(simple_ontology)
            s.agent("supervisor")
            mock_factory.assert_called_once()

    def test_agent_accepts_name_and_model_overrides(self, simple_ontology):
        import seocho.agent.factory as _factory_mod
        import seocho.store.graph as _graph_mod
        import seocho.store.llm as _llm_mod
        from seocho.client import Seocho

        with patch.object(_graph_mod, "Neo4jGraphStore"), patch.object(
            _llm_mod, "create_llm_backend"
        ), patch.object(_factory_mod, "create_indexing_agent") as mock_factory:
            mock_factory.return_value = MagicMock()
            s = Seocho.local(simple_ontology)
            s.agent("indexing", name="CustomAgent", model="gpt-4o-mini")

            call_kwargs = mock_factory.call_args.kwargs
            assert call_kwargs["name"] == "CustomAgent"
            assert call_kwargs["model"] == "gpt-4o-mini"
