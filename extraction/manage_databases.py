"""
Database provisioning script.

Creates predefined Neo4j databases and applies schemas.
Uses centralized config from config.py.
"""

import os
import logging

from neo4j import GraphDatabase

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, _VALID_DB_NAME_RE, db_registry

logger = logging.getLogger(__name__)


def create_databases(db_names):
    """Create databases in Neo4j if they don't exist.

    Requires connection to the 'system' database.
    Validates names against _VALID_DB_NAME_RE before creation.
    """
    logger.info("Connecting to %s to manage databases...", NEO4J_URI)

    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

        with driver.session(database="system") as session:
            for db_name in db_names:
                if not _VALID_DB_NAME_RE.match(db_name):
                    logger.error(
                        "Skipping invalid DB name '%s': "
                        "must be alphanumeric and start with a letter",
                        db_name,
                    )
                    continue

                try:
                    logger.info("Checking/Creating database: %s", db_name)
                    session.run(f"CREATE DATABASE {db_name} IF NOT EXISTS")
                    db_registry.register(db_name)
                    logger.info("Database '%s' ready.", db_name)
                except Exception as e:
                    logger.error("Failed to create '%s': %s", db_name, e)
                    logger.info(
                        "Note: Multi-database is an Enterprise/DozerDB feature. "
                        "Community Edition does not support this."
                    )

        driver.close()

    except Exception as e:
        logger.error("Connection to system database failed: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    target_dbs = ["kgnormal", "kgfibo", "agenttraces"]
    create_databases(target_dbs)

    # --- Schema Application ---
    from schema_manager import SchemaManager

    sm = SchemaManager(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)

    schema_map = {
        "agenttraces": "conf/schemas/tracing.yaml",
        "kgnormal": "conf/schemas/baseline.yaml",
        "kgfibo": "conf/schemas/baseline.yaml",
    }

    base_dir = os.path.dirname(os.path.abspath(__file__))

    for db, schema_file in schema_map.items():
        full_path = os.path.join(base_dir, schema_file)
        sm.apply_schema(db, full_path)

    sm.close()
