import pytest
from unittest.mock import MagicMock, patch
import yaml
import tempfile
import os

from schema_manager import SchemaManager

def test_schema_manager_escapes_backticks_and_cypher_injection():
    yaml_content = """
nodes:
  "Malicious`Label) DETACH DELETE n //":
    properties:
      "malicious`prop) DELETE n //":
        constraint: UNIQUE
        index: true
    """

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml") as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        manager = SchemaManager(uri="bolt://localhost:7687", user="test", password="test")
        manager.driver = MagicMock()
        mock_session = MagicMock()
        manager.driver.session.return_value.__enter__.return_value = mock_session

        manager.apply_schema(database="test_db", yaml_path=temp_path)

        # Verify the calls sent to session.run
        calls = mock_session.run.call_args_list
        assert len(calls) == 2 # One constraint, one index

        constraint_query = calls[0][0][0]
        index_query = calls[1][0][0]

        # Original label and prop had backticks, they should be stripped
        # Malicious`Label) DETACH DELETE n // -> MaliciousLabel) DETACH DELETE n //
        # malicious`prop) DELETE n // -> maliciousprop) DELETE n //

        expected_label = "MaliciousLabel) DETACH DELETE n //"
        expected_prop = "maliciousprop) DELETE n //"
        expected_constraint_name = f"constraint_{expected_label}_{expected_prop}_unique"
        expected_index_name = f"index_{expected_label}_{expected_prop}"

        expected_constraint_query = f"CREATE CONSTRAINT `{expected_constraint_name}` IF NOT EXISTS FOR (n:`{expected_label}`) REQUIRE n.`{expected_prop}` IS UNIQUE"
        expected_index_query = f"CREATE INDEX `{expected_index_name}` IF NOT EXISTS FOR (n:`{expected_label}`) ON (n.`{expected_prop}`)"

        assert constraint_query == expected_constraint_query
        assert index_query == expected_index_query

    finally:
        os.unlink(temp_path)
