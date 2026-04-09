"""
Managed semantic vocabulary resolver for query-time alias normalization.

The resolver loads approved semantic artifacts and exposes a lightweight
lookup path for runtime disambiguation:

- global approved vocabulary (fallback)
- workspace-specific approved vocabulary (override)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Tuple

from semantic_artifact_store import (
    DEFAULT_SEMANTIC_ARTIFACT_DIR,
    get_semantic_artifact,
    list_semantic_artifacts,
)

logger = logging.getLogger(__name__)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


class ManagedVocabularyResolver:
    """Resolve query aliases from approved semantic artifact vocabulary."""

    def __init__(
        self,
        *,
        base_dir: str | None = None,
        global_workspace_id: str | None = None,
    ) -> None:
        self.base_dir = base_dir or os.getenv("SEMANTIC_ARTIFACT_DIR", DEFAULT_SEMANTIC_ARTIFACT_DIR)
        configured_global = os.getenv("VOCABULARY_GLOBAL_WORKSPACE_ID", "global").strip()
        self.global_workspace_id = global_workspace_id or configured_global or "global"
        self.enabled = os.getenv("VOCABULARY_RESOLVER_ENABLED", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        self._cache: Dict[str, Dict[str, Any]] = {}

    def resolve_alias(self, entity_text: str, workspace_id: str = "default") -> str:
        if not self.enabled:
            return entity_text
        normalized = _normalize(entity_text)
        if not normalized:
            return entity_text
        payload = self._workspace_payload(workspace_id)
        return str(payload["aliases"].get(normalized, entity_text))

    def to_summary(self, workspace_id: str = "default") -> Dict[str, Any]:
        payload = self._workspace_payload(workspace_id)
        return {
            "enabled": self.enabled,
            "base_dir": self.base_dir,
            "workspace_id": workspace_id,
            "global_workspace_id": self.global_workspace_id,
            "alias_count": len(payload["aliases"]),
            "approved_artifact_counts": payload["approved_artifact_counts"],
        }

    def clear_cache(self) -> None:
        self._cache = {}

    def _workspace_payload(self, workspace_id: str) -> Dict[str, Any]:
        key = str(workspace_id or "default")
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        global_payload = self._collect_workspace_aliases(self.global_workspace_id)
        workspace_payload = (
            self._collect_workspace_aliases(key)
            if key != self.global_workspace_id
            else {
                "aliases": {},
                "approved_count": 0,
            }
        )

        aliases: Dict[str, str] = {}
        aliases.update(global_payload["aliases"])
        aliases.update(workspace_payload["aliases"])

        payload = {
            "aliases": aliases,
            "approved_artifact_counts": {
                "global": int(global_payload["approved_count"]),
                "workspace": int(workspace_payload["approved_count"]),
            },
        }
        self._cache[key] = payload
        return payload

    def _collect_workspace_aliases(self, workspace_id: str) -> Dict[str, Any]:
        approved_rows = list_semantic_artifacts(
            workspace_id=workspace_id,
            status="approved",
            base_dir=self.base_dir,
        )
        if not approved_rows:
            return {"aliases": {}, "approved_count": 0}

        aliases: Dict[str, str] = {}
        ordered_rows = sorted(
            approved_rows,
            key=lambda row: (
                str(row.get("approved_at") or ""),
                str(row.get("created_at") or ""),
                str(row.get("artifact_id") or ""),
            ),
        )
        for row in ordered_rows:
            artifact_id = str(row.get("artifact_id") or "").strip()
            if not artifact_id:
                continue
            try:
                payload = get_semantic_artifact(
                    workspace_id=workspace_id,
                    artifact_id=artifact_id,
                    base_dir=self.base_dir,
                )
            except FileNotFoundError:
                logger.warning(
                    "Approved semantic artifact missing on disk: workspace=%s artifact_id=%s",
                    workspace_id,
                    artifact_id,
                )
                continue
            self._merge_aliases_from_artifact(payload, aliases)
        return {"aliases": aliases, "approved_count": len(ordered_rows)}

    def _merge_aliases_from_artifact(
        self,
        payload: Dict[str, Any],
        aliases: Dict[str, str],
    ) -> None:
        vocab_candidate = payload.get("vocabulary_candidate")
        if isinstance(vocab_candidate, dict):
            for term in vocab_candidate.get("terms", []):
                if not isinstance(term, dict):
                    continue
                canonical = str(
                    term.get("canonical")
                    or term.get("pref_label")
                    or term.get("name")
                    or ""
                ).strip()
                term_aliases = term.get("aliases", [])
                if not isinstance(term_aliases, list):
                    term_aliases = []
                alt_labels = term.get("alt_labels", [])
                if not isinstance(alt_labels, list):
                    alt_labels = []
                hidden_labels = term.get("hidden_labels", [])
                if not isinstance(hidden_labels, list):
                    hidden_labels = []
                self._register_term(aliases, canonical, [*term_aliases, *alt_labels, *hidden_labels])

        ontology_candidate = payload.get("ontology_candidate", {})
        if isinstance(ontology_candidate, dict):
            for cls in ontology_candidate.get("classes", []):
                if not isinstance(cls, dict):
                    continue
                canonical = str(cls.get("name", "")).strip()
                cls_aliases = cls.get("aliases", [])
                if not isinstance(cls_aliases, list):
                    cls_aliases = []
                self._register_term(aliases, canonical, cls_aliases)

            for rel in ontology_candidate.get("relationships", []):
                if not isinstance(rel, dict):
                    continue
                canonical = str(rel.get("type", "")).strip()
                rel_aliases = rel.get("aliases", [])
                if not isinstance(rel_aliases, list):
                    rel_aliases = []
                self._register_term(aliases, canonical, rel_aliases)

        shacl_candidate = payload.get("shacl_candidate", {})
        if isinstance(shacl_candidate, dict):
            for shape in shacl_candidate.get("shapes", []):
                if not isinstance(shape, dict):
                    continue
                self._register_term(aliases, str(shape.get("target_class", "")).strip(), [])

    @staticmethod
    def _register_term(
        aliases: Dict[str, str],
        canonical: str,
        term_aliases: List[Any],
    ) -> None:
        canonical_text = str(canonical).strip()
        if not canonical_text:
            return
        values: List[str] = [canonical_text]
        for item in term_aliases:
            alias = str(item).strip()
            if alias:
                values.append(alias)
        for value in values:
            normalized = _normalize(value)
            if normalized:
                aliases[normalized] = canonical_text
