"""
Managed semantic vocabulary resolver for query-time alias normalization.

The resolver loads approved semantic artifacts and exposes a lightweight
lookup path for runtime disambiguation:

- global approved vocabulary (fallback)
- workspace-specific approved vocabulary (override)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .semantic_artifact_store import (
    DEFAULT_SEMANTIC_ARTIFACT_DIR as DEFAULT_SEMANTIC_ARTIFACT_DIR,
    get_semantic_artifact,
    list_semantic_artifacts,
)

from seocho.query.vocabulary import ManagedVocabularyResolver as _Resolver
from seocho.query.vocabulary import _normalize as _normalize

class ManagedVocabularyResolver(_Resolver):
    """Use the legacy artifact store with the SDK's alias/cache implementation."""

    def _list_semantic_artifacts(self, workspace_id: str, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return list_semantic_artifacts(workspace_id=workspace_id, status=status, base_dir=self.base_dir)

    def _get_semantic_artifact(self, workspace_id: str, artifact_id: str) -> Dict[str, Any]:
        return get_semantic_artifact(workspace_id=workspace_id, artifact_id=artifact_id, base_dir=self.base_dir)
