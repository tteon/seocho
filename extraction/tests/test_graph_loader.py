"""Tests for graph_loader label validation and loading."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, patch

# Mock neo4j before importing graph_loader
sys.modules.setdefault("neo4j", MagicMock())
sys.modules.setdefault("neo4j.exceptions", MagicMock())

from exceptions import InvalidLabelError
from graph_loader import _normalize_label, _sanitize_properties, _validate_label


class TestValidateLabel:
    def test_valid_simple_label(self):
        assert _validate_label("Company") == "Company"

    def test_valid_underscore_label(self):
        assert _validate_label("_Internal") == "_Internal"

    def test_valid_alphanumeric_label(self):
        assert _validate_label("Node123") == "Node123"

    def test_invalid_label_with_spaces(self):
        with pytest.raises(InvalidLabelError):
            _validate_label("Bad Label")

    def test_invalid_label_with_special_chars(self):
        with pytest.raises(InvalidLabelError):
            _validate_label("node;DROP")

    def test_invalid_label_starts_with_number(self):
        with pytest.raises(InvalidLabelError):
            _validate_label("123Node")

    def test_invalid_empty_label(self):
        with pytest.raises(InvalidLabelError):
            _validate_label("")

    def test_invalid_cypher_injection(self):
        with pytest.raises(InvalidLabelError):
            _validate_label("Entity` SET n.pwned=true //")

    def test_normalize_llm_label_with_spaces(self):
        assert _normalize_label("Fiscal Year") == "Fiscal_Year"

    def test_normalize_relationship_type_uppercases(self):
        assert _normalize_label("legal issue", default="RELATED_TO", uppercase=True) == "LEGAL_ISSUE"


class TestPropertySanitization:
    def test_nested_maps_are_serialized_for_neo4j_properties(self):
        props = _sanitize_properties(
            {
                "name": "Revenue",
                "amount": {"value": "$2.1 billion"},
                "tags": ["finance", "annual"],
                "nested_list": [{"year": 2023}],
                "empty": None,
            }
        )

        assert props["name"] == "Revenue"
        assert props["amount"] == '{"value": "$2.1 billion"}'
        assert props["tags"] == ["finance", "annual"]
        assert props["nested_list"] == '[{"year": 2023}]'
        assert "empty" not in props


class TestGraphLoaderLoadGraph:
    def test_load_empty_data(self):
        from graph_loader import GraphLoader

        with patch("graph_loader.GraphDatabase") as mock_gdb:
            loader = GraphLoader("bolt://test:7687", "user", "pass")
            # Should not raise or call session
            loader.load_graph({}, "src_1")
            loader.load_graph(None, "src_1")

    def test_load_valid_data(self):
        from graph_loader import GraphLoader

        with patch("graph_loader.GraphDatabase") as mock_gdb:
            mock_session = MagicMock()
            mock_driver = MagicMock()
            mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
            mock_gdb.driver.return_value = mock_driver

            loader = GraphLoader("bolt://test:7687", "user", "pass")

            data = {
                "nodes": [
                    {"id": "n1", "label": "Company", "properties": {"name": "Acme"}},
                ],
                "relationships": [
                    {"source": "n1", "target": "n2", "type": "RELATED_TO"},
                ],
            }
            loader.load_graph(data, "test_source")
            assert mock_session.execute_write.call_count == 2

    def test_create_node_normalizes_label_and_nested_properties(self):
        from graph_loader import GraphLoader

        tx = MagicMock()
        GraphLoader._create_node(
            tx,
            {
                "id": "n1",
                "label": "Fiscal Year",
                "properties": {"properties": {"amount": "$2.1 billion"}},
            },
            "src",
            "default",
        )

        query = tx.run.call_args.args[0]
        kwargs = tx.run.call_args.kwargs
        assert "MERGE (n:`Fiscal_Year`" in query
        assert kwargs["props"]["properties"] == '{"amount": "$2.1 billion"}'
        assert kwargs["props"]["source_id"] == "src"
        assert kwargs["props"]["workspace_id"] == "default"

    def test_create_relationship_sanitizes_nested_properties(self):
        from graph_loader import GraphLoader

        tx = MagicMock()
        GraphLoader._create_relationship(
            tx,
            {
                "source": "a",
                "target": "b",
                "type": "faced legal issue",
                "properties": {"properties": {}},
            },
        )

        query = tx.run.call_args.args[0]
        kwargs = tx.run.call_args.kwargs
        assert "MERGE (a)-[r:`FACED_LEGAL_ISSUE`]->(b)" in query
        assert kwargs["props"]["properties"] == "{}"
