"""Consumer contracts for experiment phases, independent of SDK construction."""

from __future__ import annotations
from typing import Any, Callable, Protocol


class IndexClient(Protocol):
    def index_file(
        self,
        path: str,
        *,
        database: str = "neo4j",
        category: str = "file",
        force: bool = False,
        strict_validation: bool | None = None,
    ) -> dict[str, Any]: ...

    def index_directory(
        self,
        directory: str,
        *,
        database: str = "neo4j",
        category: str = "file",
        recursive: bool = True,
        force: bool = False,
        on_file: Callable[[str, int, int], None] | None = None,
        strict_validation: bool | None = None,
        track: bool = True,
    ) -> dict[str, Any]: ...


class QueryClient(Protocol):
    def ask(
        self,
        message: str,
        *,
        database: str,
        reasoning_mode: bool,
        repair_budget: int,
        limit: int,
    ) -> str: ...
