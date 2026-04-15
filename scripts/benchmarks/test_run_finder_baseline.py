import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_finder_baseline as finder
import pytest


def test_default_graph_uses_embedded_when_neo4j_uri_missing(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)

    assert finder._default_graph() is None


def test_default_graph_honors_neo4j_uri(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://example:7687")

    assert finder._default_graph() == "bolt://example:7687"


def test_limit_cases_caps_rows():
    assert finder._limit_cases([1, 2, 3], 2) == [1, 2]


def test_limit_cases_rejects_non_positive_limit():
    with pytest.raises(ValueError, match="--limit"):
        finder._limit_cases([1, 2, 3], 0)
