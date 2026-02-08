"""
Centralized configuration for extraction services.

All Neo4j and shared config should be imported from here
instead of duplicating os.getenv() calls across modules.
"""

import os
import re
import logging

# Neo4j connection settings
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# Opik observability settings
OPIK_URL = os.getenv("OPIK_URL_OVERRIDE", "")
OPIK_WORKSPACE = os.getenv("OPIK_WORKSPACE", "default")
OPIK_PROJECT_NAME = os.getenv("OPIK_PROJECT_NAME", "seocho")
OPIK_ENABLED = bool(OPIK_URL)

# DB name validation: alphanumeric only, must start with a letter
_VALID_DB_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9]*$')


class DatabaseRegistry:
    """Runtime-extensible database name registry.

    Provides a central allowlist of valid Neo4j database names.
    New databases created via DatabaseManager are automatically registered.
    """

    def __init__(self):
        self._databases: set = {
            "neo4j", "system", "kgnormal", "kgfibo", "agenttraces",
        }

    def register(self, db_name: str) -> None:
        """Register a new database name after validation."""
        if not _VALID_DB_NAME_RE.match(db_name):
            raise ValueError(
                f"Invalid DB name '{db_name}': must be alphanumeric, start with letter"
            )
        self._databases.add(db_name)

    def is_valid(self, db_name: str) -> bool:
        """Check if a database name is in the registry."""
        return db_name in self._databases

    def list_databases(self) -> list:
        """Return user-facing databases (excluding system DBs)."""
        return sorted(self._databases - {"neo4j", "system"})


# Global singleton
db_registry = DatabaseRegistry()

# Legacy compat — modules that import VALID_DATABASES get a view into the registry
VALID_DATABASES = db_registry._databases


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging for extraction services."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def validate_config() -> None:
    """Validate critical configuration at startup.

    Raises:
        MissingAPIKeyError: If OPENAI_API_KEY is missing or empty.
    """
    from exceptions import MissingAPIKeyError

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise MissingAPIKeyError(
            "OPENAI_API_KEY environment variable is required but not set"
        )

    logger = logging.getLogger(__name__)
    if NEO4J_URI == "bolt://neo4j:7687":
        logger.warning(
            "Using default NEO4J_URI (%s). Set NEO4J_URI env var for production.",
            NEO4J_URI,
        )
