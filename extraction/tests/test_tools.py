
import pytest
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent to path to import tools
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


from agent_server import get_databases_impl, get_schema_impl

def test_get_databases_tool():
    result = get_databases_impl()
    assert "kgnormal" in result
    assert "kgfibo" in result

@patch("os.path.exists")
@patch("builtins.open", new_callable=MagicMock)
def test_get_schema_tool(mock_open, mock_exists):
    # Mock file existence and read
    mock_exists.return_value = True
    mock_file = MagicMock()
    mock_file.__enter__.return_value.read.return_value = "Node: Person"
    mock_open.return_value = mock_file

    result = get_schema_impl(database="kgnormal")
    assert "Node: Person" in result
    mock_open.assert_called_with("outputs/schema_baseline.yaml", "r")

def test_get_schema_tool_fallback():
    # Test fallback when file doesn't exist
    with patch("os.path.exists", return_value=False):
        result = get_schema_impl(database="unknown_db")
        assert "Schema file" in result
        assert "not found" in result
