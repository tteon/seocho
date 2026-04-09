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
from typing import Any, Dict, List, Optional

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


@dataclass(frozen=True)
class GraphTarget:
    graph_id: str
    database: str
    uri: str = NEO4J_URI
    user: str = NEO4J_USER
    password: str = NEO4J_PASSWORD
    ontology_id: str = "baseline"
    vocabulary_profile: str = "vocabulary.v2"
    description: str = ""
    workspace_scope: str = "default"

    def to_public_dict(self) -> Dict[str, Any]:
        """Return a safe public descriptor without credentials."""
        return {
            "graph_id": self.graph_id,
            "database": self.database,
            "uri": self.uri,
            "ontology_id": self.ontology_id,
            "vocabulary_profile": self.vocabulary_profile,
            "description": self.description,
            "workspace_scope": self.workspace_scope,
        }


class GraphRegistry:
    """Registry for graph-scoped runtime targets.

    Graph IDs are the public routing identifiers used by debate/runtime APIs.
    Each graph target can point to a different Neo4j/DozerDB instance.
    """

    def __init__(
        self,
        database_registry: DatabaseRegistry,
        defaults: Optional[List[GraphTarget]] = None,
    ):
        self._database_registry = database_registry
        self._graphs: Dict[str, GraphTarget] = {}
        for target in defaults or []:
            self.register_target(target)

    def register(
        self,
        graph_id: str,
        database: Optional[str] = None,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        ontology_id: Optional[str] = None,
        vocabulary_profile: str = "vocabulary.v2",
        description: str = "",
        workspace_scope: str = "default",
    ) -> GraphTarget:
        target = GraphTarget(
            graph_id=graph_id,
            database=database or graph_id,
            uri=uri or NEO4J_URI,
            user=user or NEO4J_USER,
            password=password or NEO4J_PASSWORD,
            ontology_id=ontology_id or graph_id,
            vocabulary_profile=vocabulary_profile,
            description=description,
            workspace_scope=workspace_scope,
        )
        return self.register_target(target)

    def register_target(self, target: GraphTarget) -> GraphTarget:
        if not _VALID_DB_NAME_RE.match(target.graph_id):
            raise ValueError(
                f"Invalid graph_id '{target.graph_id}': must be alphanumeric, start with letter"
            )
        if not _VALID_DB_NAME_RE.match(target.database):
            raise ValueError(
                f"Invalid database '{target.database}': must be alphanumeric, start with letter"
            )
        self._graphs[target.graph_id] = target
        self._database_registry.register(target.database)
        return target

    def ensure_default_graph(
        self,
        database: str,
        *,
        graph_id: Optional[str] = None,
        ontology_id: Optional[str] = None,
        vocabulary_profile: str = "vocabulary.v2",
        description: str = "",
    ) -> GraphTarget:
        resolved_graph_id = graph_id or database
        existing = self._graphs.get(resolved_graph_id)
        if existing is not None:
            return existing
        return self.register(
            graph_id=resolved_graph_id,
            database=database,
            ontology_id=ontology_id or database,
            vocabulary_profile=vocabulary_profile,
            description=description or f"Graph target for database '{database}'.",
        )

    def get_graph(self, graph_id: str) -> Optional[GraphTarget]:
        return self._graphs.get(graph_id)

    def find_by_database(self, database: str) -> Optional[GraphTarget]:
        for target in self._graphs.values():
            if target.database == database:
                return target
        return None

    def is_valid_graph(self, graph_id: str) -> bool:
        return graph_id in self._graphs

    def list_graph_ids(self) -> List[str]:
        return sorted(self._graphs.keys())

    def list_graphs(self) -> List[GraphTarget]:
        return [self._graphs[graph_id] for graph_id in self.list_graph_ids()]


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


def _default_graph_targets() -> List[GraphTarget]:
    return [
        GraphTarget(
            graph_id="kgnormal",
            database="kgnormal",
            ontology_id="baseline",
            vocabulary_profile="vocabulary.v2",
            description="Baseline enterprise graph for general entity and relation retrieval.",
        ),
        GraphTarget(
            graph_id="kgfibo",
            database="kgfibo",
            ontology_id="fibo",
            vocabulary_profile="vocabulary.v2",
            description="Financial ontology graph aligned to FIBO-style concepts.",
        ),
    ]


def _load_graph_targets() -> List[GraphTarget]:
    config_path = Path(
        os.getenv(
            "SEOCHO_GRAPH_REGISTRY_FILE",
            Path(__file__).resolve().parent / "conf" / "graphs" / "default.yaml",
        )
    )
    payload = _load_yaml(config_path)
    graph_items = payload.get("graphs", [])
    if not isinstance(graph_items, list) or not graph_items:
        return _default_graph_targets()

    targets: List[GraphTarget] = []
    for raw in graph_items:
        if not isinstance(raw, dict):
            continue
        graph_id = str(raw.get("graph_id", "")).strip()
        database = str(raw.get("database", graph_id)).strip()
        if not graph_id or not database:
            continue
        targets.append(
            GraphTarget(
                graph_id=graph_id,
                database=database,
                uri=str(raw.get("uri") or NEO4J_URI),
                user=str(raw.get("user") or NEO4J_USER),
                password=str(raw.get("password") or NEO4J_PASSWORD),
                ontology_id=str(raw.get("ontology_id") or graph_id),
                vocabulary_profile=str(raw.get("vocabulary_profile") or "vocabulary.v2"),
                description=str(raw.get("description") or ""),
                workspace_scope=str(raw.get("workspace_scope") or "default"),
            )
        )
    return targets or _default_graph_targets()


# Global singletons
db_registry = DatabaseRegistry()
graph_registry = GraphRegistry(db_registry, _load_graph_targets())

# Legacy compat — modules that import VALID_DATABASES get a view into the registry
VALID_DATABASES = db_registry._databases


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
