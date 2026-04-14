import logging
import yaml
import os
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from exceptions import Neo4jConnectionError
from retry_utils import neo4j_retry

logger = logging.getLogger(__name__)

class SchemaManager:
    def __init__(self, uri=None, user=None, password=None):
        self.uri = uri or NEO4J_URI
        self.user = user or NEO4J_USER
        self.password = password or NEO4J_PASSWORD
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def close(self):
        self.driver.close()

    @neo4j_retry
    def apply_schema(self, database, yaml_path):
        """
        Reads a YAML schema definition and applies constraints/indexes.

        Raises:
            Neo4jConnectionError: On transient Neo4j failures (retried automatically).
        """
        logger.info("Reading schema from %s for DB '%s'...", yaml_path, database)

        if not os.path.exists(yaml_path):
            logger.warning("Schema file %s not found. Skipping.", yaml_path)
            return

        with open(yaml_path, 'r') as f:
            schema = yaml.safe_load(f)

        nodes = schema.get('nodes', {})

        try:
            with self.driver.session(database=database) as session:
                for label, definition in nodes.items():
                    props = definition.get('properties', {})
                    for prop_name, config in props.items():
                        # 1. Unique Constraints
                        if config.get('constraint') == 'UNIQUE':
                            safe_label = label.replace('`', '')
                            safe_prop = prop_name.replace('`', '')
                            constraint_name = f"constraint_{safe_label}_{safe_prop}_unique"
                            query = f"CREATE CONSTRAINT `{constraint_name}` IF NOT EXISTS FOR (n:`{safe_label}`) REQUIRE n.`{safe_prop}` IS UNIQUE"
                            try:
                                session.run(query)
                                logger.info("Applied UNIQUE constraint on :%s(%s)", safe_label, safe_prop)
                            except (ServiceUnavailable, SessionExpired) as e:
                                raise Neo4jConnectionError(
                                    f"Neo4j connection failed applying constraint on :{safe_label}({safe_prop}): {e}"
                                ) from e
                            except Exception as e:
                                logger.error("Failed to apply constraint on :%s(%s): %s", safe_label, safe_prop, e)

                        # 2. Indexes
                        if config.get('index') is True:
                            safe_label = label.replace('`', '')
                            safe_prop = prop_name.replace('`', '')
                            index_name = f"index_{safe_label}_{safe_prop}"
                            query = f"CREATE INDEX `{index_name}` IF NOT EXISTS FOR (n:`{safe_label}`) ON (n.`{safe_prop}`)"
                            try:
                                session.run(query)
                                logger.info("Applied INDEX on :%s(%s)", safe_label, safe_prop)
                            except (ServiceUnavailable, SessionExpired) as e:
                                raise Neo4jConnectionError(
                                    f"Neo4j connection failed applying index on :{safe_label}({safe_prop}): {e}"
                                ) from e
                            except Exception as e:
                                logger.error("Failed to apply index on :%s(%s): %s", safe_label, safe_prop, e)
        except (ServiceUnavailable, SessionExpired) as e:
            raise Neo4jConnectionError(
                f"Neo4j connection failed during schema application for '{database}': {e}"
            ) from e

        logger.info("Schema application for '%s' complete.", database)

    def update_schema_from_records(self, records: dict, yaml_path: str):
        """
        Scans extracted records (nodes/relationships) and updates the YAML schema.
        """
        logger.info("Scanning records for schema updates in %s...", yaml_path)

        if not os.path.exists(yaml_path):
            logger.warning("Schema file %s not found. Creating new.", yaml_path)
            current_schema = {"graph_type": "AutoDiscovered", "version": "1.0", "nodes": {}, "relationships": {}}
        else:
            with open(yaml_path, 'r') as f:
                current_schema = yaml.safe_load(f) or {}

        nodes_config = current_schema.get("nodes", {})
        rels_config = current_schema.get("relationships", {})

        # 1. Discover Nodes
        for node in records.get("nodes", []):
            label = node.get("label", "Unknown")
            if label not in nodes_config:
                logger.info("Discovered new Node Label: %s", label)
                nodes_config[label] = {
                    "description": f"Auto-discovered node type: {label}",
                    "properties": {
                        "id": {"type": "STRING", "constraint": "UNIQUE"},
                        "name": {"type": "STRING", "index": "TRUE"}
                    }
                }

        # 2. Discover Relationships
        for rel in records.get("relationships", []):
            rel_type = rel.get("type", "RELATED_TO")
            if rel_type not in rels_config:
                logger.info("Discovered new Relationship Type: %s", rel_type)
                rels_config[rel_type] = {
                    "source": "Any",
                    "target": "Any"
                }

        # 3. Save Updates
        current_schema["nodes"] = nodes_config
        current_schema["relationships"] = rels_config

        with open(yaml_path, 'w') as f:
            yaml.dump(current_schema, f, sort_keys=False)

        logger.info("Schema YAML updated.")
