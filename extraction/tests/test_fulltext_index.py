import json
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fulltext_index import FulltextIndexManager


class FakeConnector:
    def __init__(self):
        self.created = False

    def run_cypher(self, query, database="neo4j", params=None):
        if "SHOW FULLTEXT INDEXES" in query or "SHOW INDEXES" in query:
            if self.created:
                return json.dumps(
                    [
                        {
                            "name": "entity_fulltext",
                            "state": "ONLINE",
                            "entityType": "NODE",
                            "labelsOrTypes": ["Entity"],
                            "properties": ["name"],
                        }
                    ]
                )
            return json.dumps([])

        if "CREATE FULLTEXT INDEX" in query:
            self.created = True
            return json.dumps([])

        if "CALL db.index.fulltext.createNodeIndex" in query:
            self.created = True
            return json.dumps([])

        return json.dumps([])


def test_ensure_index_creates_when_missing():
    manager = FulltextIndexManager(FakeConnector())
    result = manager.ensure_index(
        database="kgnormal",
        index_name="entity_fulltext",
        labels=["Entity"],
        properties=["name"],
        create_if_missing=True,
    )
    assert result["exists"] is True
    assert result["created"] is True


def test_ensure_index_reports_not_found_when_create_disabled():
    manager = FulltextIndexManager(FakeConnector())
    result = manager.ensure_index(
        database="kgnormal",
        index_name="entity_fulltext",
        labels=["Entity"],
        properties=["name"],
        create_if_missing=False,
    )
    assert result["exists"] is False
    assert result["created"] is False
