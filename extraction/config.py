"""
Centralized configuration for extraction services.

All Neo4j and shared config should be imported from here
instead of duplicating os.getenv() calls across modules.
"""

import os
import re
import logging
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

# DozerDB connection settings (primary)
# Keep NEO4J_* aliases for compatibility because DozerDB is Neo4j-protocol compatible.
DOZERDB_URI = os.getenv("DOZERDB_URI", os.getenv("NEO4J_URI", "bolt://neo4j:7687"))
DOZERDB_USER = os.getenv("DOZERDB_USER", os.getenv("NEO4J_USER", "neo4j"))
DOZERDB_PASSWORD = os.getenv("DOZERDB_PASSWORD", os.getenv("NEO4J_PASSWORD", "password"))

# Backward-compatible aliases used by existing modules
NEO4J_URI = DOZERDB_URI
NEO4J_USER = DOZERDB_USER
NEO4J_PASSWORD = DOZERDB_PASSWORD

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
        return sorted(self._databases - {"neo4j", "system", "agenttraces"})


# Global singleton
db_registry = DatabaseRegistry()

# Legacy compat — modules that import VALID_DATABASES get a view into the registry
VALID_DATABASES = db_registry._databases


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class PromptTemplates:
    system: str
    user: str


@dataclass
class LinkingPromptTemplates:
    linking: str


@dataclass
class PipelineRuntimeConfig:
    model: str
    mock_data: bool
    enable_rule_constraints: bool
    openai_api_key: str
    prompts: PromptTemplates
    linking_prompt: LinkingPromptTemplates

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if isinstance(payload, dict):
        return payload
    return {}


def load_pipeline_runtime_config(prompts_dir: Path | None = None) -> PipelineRuntimeConfig:
    """Load pipeline config without Hydra/OmegaConf dependency."""
    base_dir = prompts_dir or (Path(__file__).resolve().parent / "conf" / "prompts")
    default_prompt = _load_yaml(base_dir / "default.yaml")
    linking_prompt = _load_yaml(base_dir / "linking.yaml")

    return PipelineRuntimeConfig(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        mock_data=_env_bool("EXTRACTION_MOCK_DATA", True),
        enable_rule_constraints=_env_bool("ENABLE_RULE_CONSTRAINTS", True),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        prompts=PromptTemplates(
            system=str(default_prompt.get("system", "")),
            user=str(default_prompt.get("user", "")),
        ),
        linking_prompt=LinkingPromptTemplates(
            linking=str(linking_prompt.get("linking", "")),
        ),
    )


def to_namespace(payload: Any) -> Any:
    """Recursively convert dict/list payloads to attribute namespaces."""
    if isinstance(payload, dict):
        return SimpleNamespace(**{key: to_namespace(value) for key, value in payload.items()})
    if isinstance(payload, list):
        return [to_namespace(value) for value in payload]
    return payload


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
