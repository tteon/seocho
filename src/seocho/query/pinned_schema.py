"""Resolve a pinned ontology version to its FROZEN schema + policy (B1 fix).

The run-context (ADR-0200) records which ontology (version, fingerprint) a request
pinned, but the pin alone is a metadata stamp — nothing turned it into the actual
frozen schema the query specialist must read. Without this, the specialist would
fall back to ``schema_for_prompt(self.ontology)`` on the LIVE, mutable ontology, and
a mid-request publish would still change what it reads: "the OS delivers ONE pinned
ontology per request" would be asserted, not real.

``PinnedSchemaResolver`` closes that: given ``(package_id, version)`` it loads the
immutable snapshot (``OntologySnapshotStore``), compiles the prompt schema AND the
Cypher-validation policy from THAT SAME frozen ontology (so the prompt the specialist
sees and the guardrail that enforces it can never disagree — B3), and returns them as
one resolved handle threaded per request.

Caching (B6): the compiled schema block is **pure, tenant-agnostic data** — it depends
only on ``(package_id, version, fingerprint)``, never on a workspace. So it is safe to
cache by that key and share across tenants. We cache ONLY this data — never a
workspace-bound Agent or tool (that was the cross-tenant leak the review flagged).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .hybrid_planner import policy_from_ontology, schema_for_prompt


@dataclass(frozen=True)
class ResolvedSchema:
    """The frozen ontology snapshot a request pinned, compiled for use.

    ``ontology`` / ``policy`` / ``schema`` all derive from ONE immutable snapshot,
    so the prompt schema and the guardrail policy are guaranteed consistent."""

    package_id: str
    version: str
    fingerprint: str
    ontology: Any                 # the frozen Ontology loaded from the snapshot
    policy: Any                   # Text2CypherFallbackPolicy from the SAME snapshot
    schema: Dict[str, Any]        # schema_for_prompt(...) from the SAME snapshot

    def schema_text(self) -> str:
        """Compact JSON rendering of the schema block for prompt injection."""
        return json.dumps(
            {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.schema.items()},
            default=str,
        )


class PinnedSchemaResolver:
    """Resolve pinned ``(package_id, version)`` → :class:`ResolvedSchema`, caching the
    tenant-agnostic compiled block by ``(package_id, version, fingerprint)``."""

    def __init__(self, snapshot_store: Any) -> None:
        self._store = snapshot_store
        self._cache: Dict[Tuple[str, str, str], ResolvedSchema] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def resolve(self, package_id: str, version: str) -> Optional[ResolvedSchema]:
        """Return the frozen, compiled schema+policy for the pinned version, or
        ``None`` if the snapshot store has no such version."""
        snap = self._store.get(package_id, version)
        if snap is None:
            return None
        key = (package_id, version, snap.schema_fingerprint)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self.hits += 1
                return cached
        # compile OUTSIDE the lock (pure), then publish under it (first-writer-wins)
        onto = snap.load_ontology()               # the FROZEN snapshot ontology
        policy = policy_from_ontology(onto)
        schema = schema_for_prompt(onto, policy)
        resolved = ResolvedSchema(
            package_id=package_id, version=version, fingerprint=snap.schema_fingerprint,
            ontology=onto, policy=policy, schema=schema,
        )
        with self._lock:
            existing = self._cache.get(key)
            if existing is not None:
                self.hits += 1
                return existing
            self._cache[key] = resolved
            self.misses += 1
        return resolved

    def resolve_for(self, run_context: Any) -> Optional[ResolvedSchema]:
        """Resolve the schema a run-context pinned. Reads the pinned version from
        the run-context metadata (``pinned_ontology_version``); returns ``None``
        when the request pinned nothing (no active version)."""
        version = (run_context.metadata or {}).get("pinned_ontology_version")
        package_id = run_context.ontology_id or ""
        if not version or not package_id:
            return None
        return self.resolve(str(package_id), str(version))

    def stats(self) -> Dict[str, int]:
        return {"entries": len(self._cache), "hits": self.hits, "misses": self.misses}
