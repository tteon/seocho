"""Runtime ontology registry — populates the active hash bridge for Phases 1/2/3.

Phases 1, 2, 3 plumbed three parameters that fire ontology context hash
drift detection: ``active_context_hashes`` on
``memory_service.ontology_context_mismatch``, ``ontology_contexts`` on
``AgentFactory.create_agents_for_graphs``, and ``ontology_context_skew`` on
``AgentStateMachine``. None of those parameters had a producer because the
runtime carried no in-memory ``Ontology`` object per registered graph.

This module is that producer. Operators describe the ontology layout through
a JSON manifest (env: ``SEOCHO_RUNTIME_ONTOLOGIES``); the registry
deserializes each ontology, compiles its ``CompiledOntologyContext`` once,
and exposes the two accessors the debate handler uses on every request.

Manifest schema::

    [
      {
        "graph_id": "kgnormal",
        "database": "kgnormal",
        "ontology_path": "/path/to/finance.jsonld",
        "workspace_id": "default",      # optional, defaults to "default"
        "profile": "default"             # optional, defaults to "default"
      },
      ...
    ]

JSON-LD and YAML are both accepted (suffix-detected). Missing or malformed
entries are logged at warning level and skipped — a bad manifest line for one
graph must not take the runtime offline. Without a manifest, the registry
stays empty and Phases 1/2/3 remain inert (their unset-default behavior).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from seocho.ontology import Ontology
from seocho.ontology_context import CompiledOntologyContext, compile_ontology_context

logger = logging.getLogger(__name__)


_DEFAULT_WORKSPACE = "default"
_DEFAULT_PROFILE = "default"
_ENV_MANIFEST_PATH = "SEOCHO_RUNTIME_ONTOLOGIES"


class RuntimeOntologyRegistry:
    """Thread-safe registry of {graph_id: Ontology + CompiledOntologyContext}.

    Lookups are keyed by ``(workspace_id, graph_id)`` so multi-workspace
    futures don't require an API rewrite. The single-tenant MVP populates
    only ``workspace_id="default"`` today; Phase 4b can split per workspace.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # (workspace_id, graph_id) -> Ontology
        self._ontologies: Dict[tuple[str, str], Ontology] = {}
        # (workspace_id, graph_id) -> CompiledOntologyContext
        self._contexts: Dict[tuple[str, str], CompiledOntologyContext] = {}
        # (workspace_id, graph_id) -> database
        self._database_for: Dict[tuple[str, str], str] = {}

    def register(
        self,
        graph_id: str,
        database: str,
        ontology: Ontology,
        *,
        workspace_id: str = _DEFAULT_WORKSPACE,
        profile: str = _DEFAULT_PROFILE,
    ) -> CompiledOntologyContext:
        """Register an ontology for a graph and return its compiled context.

        Re-registering an existing ``(workspace_id, graph_id)`` replaces the
        previous entry. Compilation is eager so the descriptor's
        ``context_hash`` is stable for the lifetime of the registration.
        """

        if not isinstance(ontology, Ontology):
            raise TypeError(
                f"register() requires an Ontology instance, got {type(ontology).__name__}"
            )
        gid = str(graph_id or "").strip()
        db = str(database or "").strip()
        wid = str(workspace_id or _DEFAULT_WORKSPACE).strip() or _DEFAULT_WORKSPACE
        if not gid or not db:
            raise ValueError("graph_id and database must be non-empty.")

        compiled = compile_ontology_context(
            ontology,
            workspace_id=wid,
            profile=str(profile or _DEFAULT_PROFILE),
        )
        key = (wid, gid)
        with self._lock:
            self._ontologies[key] = ontology
            self._contexts[key] = compiled
            self._database_for[key] = db
        logger.info(
            "Registered runtime ontology graph_id=%s database=%s workspace=%s context_hash=%s",
            gid,
            db,
            wid,
            compiled.descriptor.context_hash,
        )
        return compiled

    def get_ontology(
        self,
        graph_id: str,
        *,
        workspace_id: str = _DEFAULT_WORKSPACE,
    ) -> Optional[Ontology]:
        with self._lock:
            return self._ontologies.get((workspace_id, graph_id))

    def get_context(
        self,
        graph_id: str,
        *,
        workspace_id: str = _DEFAULT_WORKSPACE,
    ) -> Optional[CompiledOntologyContext]:
        with self._lock:
            return self._contexts.get((workspace_id, graph_id))

    def active_context_hashes(
        self,
        *,
        workspace_id: str = _DEFAULT_WORKSPACE,
    ) -> Dict[str, str]:
        """Return ``{database: context_hash}`` for the workspace.

        Consumed by ``memory_service.ontology_context_mismatch`` (Phase 1
        plumbing) so ``assess_graph_ontology_context_status`` can compare
        graph-stamped hashes against the runtime's active hash.
        """

        with self._lock:
            return {
                self._database_for[key]: ctx.descriptor.context_hash
                for key, ctx in self._contexts.items()
                if key[0] == workspace_id and key in self._database_for
            }

    def ontology_contexts(
        self,
        *,
        workspace_id: str = _DEFAULT_WORKSPACE,
    ) -> Dict[str, CompiledOntologyContext]:
        """Return ``{graph_id: CompiledOntologyContext}`` for the workspace.

        Consumed by ``AgentFactory.create_agents_for_graphs`` (Phase 2
        plumbing) so the agent-creation skew probe runs against the
        runtime's active ontology.
        """

        with self._lock:
            return {
                key[1]: ctx
                for key, ctx in self._contexts.items()
                if key[0] == workspace_id
            }

    def clear(self) -> None:
        with self._lock:
            self._ontologies.clear()
            self._contexts.clear()
            self._database_for.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._ontologies)


# Module-level singleton mirroring the other server_runtime patterns.
_RUNTIME_ONTOLOGY_REGISTRY: Optional[RuntimeOntologyRegistry] = None
_REGISTRY_LOCK = threading.Lock()


def get_runtime_ontology_registry() -> RuntimeOntologyRegistry:
    global _RUNTIME_ONTOLOGY_REGISTRY
    if _RUNTIME_ONTOLOGY_REGISTRY is None:
        with _REGISTRY_LOCK:
            if _RUNTIME_ONTOLOGY_REGISTRY is None:
                _RUNTIME_ONTOLOGY_REGISTRY = RuntimeOntologyRegistry()
    return _RUNTIME_ONTOLOGY_REGISTRY


def reset_runtime_ontology_registry() -> None:
    """Test seam — drop the singleton so each test starts from empty."""
    global _RUNTIME_ONTOLOGY_REGISTRY
    with _REGISTRY_LOCK:
        if _RUNTIME_ONTOLOGY_REGISTRY is not None:
            _RUNTIME_ONTOLOGY_REGISTRY.clear()
        _RUNTIME_ONTOLOGY_REGISTRY = None


# ---------------------------------------------------------------------------
# Manifest loaders
# ---------------------------------------------------------------------------


def _load_ontology_from_path(path: Path) -> Ontology:
    suffix = path.suffix.lower()
    if suffix in {".jsonld", ".json"}:
        return Ontology.from_jsonld(path)
    if suffix in {".yaml", ".yml"}:
        return Ontology.from_yaml(path)
    raise ValueError(
        f"Unsupported ontology file suffix: {path.suffix}. "
        f"Use .jsonld, .json, .yaml, or .yml."
    )


def load_runtime_ontologies_from_manifest(
    manifest_path: str | Path,
    *,
    registry: Optional[RuntimeOntologyRegistry] = None,
) -> int:
    """Load ontologies described by a JSON manifest into the registry.

    Returns the number of ontologies successfully registered. Per-entry
    failures are logged at warning level and skipped so one bad path can't
    take the runtime offline.
    """

    target = registry if registry is not None else get_runtime_ontology_registry()
    path = Path(manifest_path).expanduser()
    if not path.exists():
        logger.info("Runtime ontology manifest not found: %s (registry stays empty).", path)
        return 0

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read runtime ontology manifest %s: %s", path, exc)
        return 0

    if not isinstance(payload, list):
        logger.warning(
            "Runtime ontology manifest must be a JSON array, got %s — registry stays empty.",
            type(payload).__name__,
        )
        return 0

    manifest_dir = path.parent
    loaded = 0
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            logger.warning(
                "Runtime ontology manifest entry %d is not an object; skipping.",
                index,
            )
            continue
        graph_id = str(entry.get("graph_id", "")).strip()
        database = str(entry.get("database", "")).strip()
        ontology_path_raw = str(entry.get("ontology_path", "")).strip()
        if not graph_id or not database or not ontology_path_raw:
            logger.warning(
                "Runtime ontology manifest entry %d is missing graph_id/database/ontology_path; skipping.",
                index,
            )
            continue

        ontology_path = Path(ontology_path_raw).expanduser()
        if not ontology_path.is_absolute():
            ontology_path = (manifest_dir / ontology_path).resolve()

        try:
            ontology = _load_ontology_from_path(ontology_path)
        except Exception as exc:
            logger.warning(
                "Failed to load ontology for graph_id=%s from %s: %s",
                graph_id,
                ontology_path,
                exc,
            )
            continue

        try:
            target.register(
                graph_id,
                database,
                ontology,
                workspace_id=str(entry.get("workspace_id", _DEFAULT_WORKSPACE)).strip()
                or _DEFAULT_WORKSPACE,
                profile=str(entry.get("profile", _DEFAULT_PROFILE)).strip()
                or _DEFAULT_PROFILE,
            )
        except Exception as exc:
            logger.warning(
                "Failed to register ontology for graph_id=%s: %s",
                graph_id,
                exc,
            )
            continue

        loaded += 1

    logger.info(
        "Runtime ontology registry loaded %d/%d entries from %s.",
        loaded,
        len(payload),
        path,
    )
    return loaded


def load_runtime_ontologies_from_env(
    *,
    registry: Optional[RuntimeOntologyRegistry] = None,
) -> int:
    """Load ontologies from the path in ``SEOCHO_RUNTIME_ONTOLOGIES``.

    Returns 0 when the env var is unset or the file is missing — runtime
    boot must succeed without an ontology manifest (Phases 1/2/3 stay
    inert in that case, matching their backward-compatible default).
    """

    raw = os.getenv(_ENV_MANIFEST_PATH, "").strip()
    if not raw:
        return 0
    return load_runtime_ontologies_from_manifest(raw, registry=registry)
