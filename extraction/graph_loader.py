import logging
import re
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired
from exceptions import Neo4jConnectionError, InvalidLabelError, LoadError
from retry_utils import neo4j_retry

logger = logging.getLogger(__name__)

# Regex for valid Neo4j label/relationship type names
_VALID_LABEL_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _validate_label(label: str) -> str:
    """Validate and sanitize a Neo4j label or relationship type.

    Returns the label if valid, raises InvalidLabelError otherwise.
    """
    if _VALID_LABEL_RE.match(label):
        return label
    raise InvalidLabelError(f"Invalid Neo4j label: '{label}'")


class GraphLoader:
    def __init__(self, uri, username, password):
        self.driver = GraphDatabase.driver(uri, auth=(username, password))

    def close(self):
        self.driver.close()

    @neo4j_retry
    def load_graph(self, graph_data: dict, source_id: str, database: str = "neo4j"):
        """
        Loads nodes and relationships into Neo4j.

        Raises:
            Neo4jConnectionError: On transient Neo4j failures (retried automatically).
            LoadError: On data/validation issues during loading.
        """
        if not graph_data or "nodes" not in graph_data:
            return

        try:
            with self.driver.session(database=database) as session:
                # 1. Load Nodes
                for node in graph_data.get("nodes", []):
                    session.execute_write(self._create_node, node, source_id)

                # 2. Load Relationships
                for rel in graph_data.get("relationships", []):
                    session.execute_write(self._create_relationship, rel)
        except (ServiceUnavailable, SessionExpired) as e:
            raise Neo4jConnectionError(f"Neo4j connection failed during load: {e}") from e
        except (Neo4jConnectionError, LoadError, InvalidLabelError):
            raise
        except Exception as e:
            raise LoadError(f"Graph loading failed for source '{source_id}': {e}") from e

    @staticmethod
    def _create_node(tx, node, source_id):
        label = _validate_label(node.get("label", "Entity"))
        properties = node.get("properties", {})
        properties["id"] = node["id"]
        properties["source_id"] = source_id

        query = (
            f"MERGE (n:`{label}` {{id: $id}}) "
            f"SET n += $props "
            f"RETURN n"
        )
        tx.run(query, id=node["id"], props=properties)

    @staticmethod
    def _create_relationship(tx, rel):
        source_id = rel["source"]
        target_id = rel["target"]
        rel_type = _validate_label(
            rel.get("type", "RELATED_TO").upper().replace(" ", "_")
        )
        properties = rel.get("properties", {})

        query = (
            f"MATCH (a {{id: $source_id}}), (b {{id: $target_id}}) "
            f"MERGE (a)-[r:`{rel_type}`]->(b) "
            f"SET r += $props "
            f"RETURN r"
        )
        tx.run(query, source_id=source_id, target_id=target_id, props=properties)
