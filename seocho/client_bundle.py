from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from .runtime_bundle import build_runtime_bundle, create_client_from_runtime_bundle

if TYPE_CHECKING:
    from .client import Seocho
    from .runtime_bundle import RuntimeBundle


class RuntimeBundleClientHelper:
    """Own runtime-bundle import/export glue behind the SDK facade."""

    def export_bundle(
        self,
        client: "Seocho",
        path: Optional[str] = None,
        *,
        app_name: Optional[str] = None,
        default_database: str = "neo4j",
    ) -> "RuntimeBundle":
        bundle = build_runtime_bundle(
            client,
            app_name=app_name,
            default_database=default_database,
        )
        if path:
            bundle.save(path)
        return bundle

    @staticmethod
    def create_client(
        bundle_source: "RuntimeBundle | str | Path",
        *,
        workspace_id: Optional[str] = None,
    ) -> Any:
        return create_client_from_runtime_bundle(bundle_source, workspace_id=workspace_id)
