"""Notion connector materialization helpers.

The live client is read-only. It fetches pages / data-source rows and renders
them as ``ConnectorRecord`` instances suitable for JSONL export.
"""

from __future__ import annotations

import os
import time
from typing import Any, Iterable, Iterator, Mapping, Optional

import requests

from .records import ConnectorRecord, stable_record_id

DEFAULT_NOTION_VERSION = "2026-03-11"


class ConnectorAPIError(RuntimeError):
    """Raised when an external connector API returns an error."""


def _rich_text_plain(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    return "".join(str(item.get("plain_text") or "") for item in items if isinstance(item, Mapping))


def _property_value(prop: Mapping[str, Any]) -> Any:
    kind = str(prop.get("type") or "")
    value = prop.get(kind)
    if kind in {"title", "rich_text"}:
        return _rich_text_plain(value)
    if kind in {"select", "status"} and isinstance(value, Mapping):
        return value.get("name")
    if kind == "multi_select" and isinstance(value, list):
        return [item.get("name") for item in value if isinstance(item, Mapping)]
    if kind == "date" and isinstance(value, Mapping):
        return value.get("start")
    if kind in {"number", "checkbox", "email", "phone_number", "url", "created_time", "last_edited_time"}:
        return value
    if kind in {"people", "relation"} and isinstance(value, list):
        return [item.get("id") for item in value if isinstance(item, Mapping)]
    if kind in {"created_by", "last_edited_by"} and isinstance(value, Mapping):
        return value.get("id")
    return value


def page_title(page: Mapping[str, Any]) -> str:
    properties = page.get("properties") if isinstance(page.get("properties"), Mapping) else {}
    for prop in properties.values():
        if isinstance(prop, Mapping) and prop.get("type") == "title":
            title = _rich_text_plain(prop.get("title"))
            if title:
                return title
    return "Untitled Notion page"


def page_metadata(page: Mapping[str, Any]) -> dict[str, Any]:
    properties = page.get("properties") if isinstance(page.get("properties"), Mapping) else {}
    values = {
        str(name): _property_value(prop)
        for name, prop in properties.items()
        if isinstance(prop, Mapping)
    }
    return {
        "external_id": page.get("id"),
        "notion_parent": page.get("parent"),
        "notion_properties": values,
        "archived": bool(page.get("archived")),
        "in_trash": bool(page.get("in_trash")),
    }


def block_to_text(block: Mapping[str, Any]) -> str:
    kind = str(block.get("type") or "")
    body = block.get(kind) if isinstance(block.get(kind), Mapping) else {}
    text = _rich_text_plain(body.get("rich_text"))
    if kind.startswith("heading_") and text:
        level = kind.rsplit("_", 1)[-1]
        hashes = "#" * int(level) if level.isdigit() else "#"
        return f"{hashes} {text}"
    if kind in {"bulleted_list_item", "to_do"} and text:
        return f"- {text}"
    if kind == "numbered_list_item" and text:
        return f"1. {text}"
    if kind == "quote" and text:
        return f"> {text}"
    if kind == "code" and text:
        language = str(body.get("language") or "")
        return f"```{language}\n{text}\n```"
    if kind == "child_page":
        return f"# {body.get('title', 'Untitled child page')}"
    if kind == "child_database":
        return f"# {body.get('title', 'Untitled child database')}"
    return text


def blocks_to_markdown(blocks: Iterable[Mapping[str, Any]]) -> str:
    lines = [text for block in blocks if (text := block_to_text(block)).strip()]
    return "\n\n".join(lines)


def page_to_record(
    page: Mapping[str, Any],
    *,
    block_text: str = "",
    category: str = "notion",
    workspace_id: str = "",
) -> ConnectorRecord:
    title = page_title(page)
    content = block_text.strip() or title
    external_id = str(page.get("id") or "")
    metadata = page_metadata(page)
    metadata.update({"workspace_id": workspace_id, "notion_object": page.get("object")})
    return ConnectorRecord(
        id=stable_record_id("notion", external_id, content),
        content=content,
        provider="notion",
        source_kind="notion_page",
        category=category,
        title=title,
        url=str(page.get("url") or ""),
        created_at=str(page.get("created_time") or ""),
        updated_at=str(page.get("last_edited_time") or ""),
        metadata=metadata,
    )


class NotionClient:
    """Small read-only Notion REST client.

    ``token_env`` is stored in configs / CLI arguments; the token value is read
    at runtime and never recorded in generated metadata.
    """

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        token_env: str = "NOTION_TOKEN",
        notion_version: str = DEFAULT_NOTION_VERSION,
        base_url: str = "https://api.notion.com",
        session: Optional[requests.Session] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        resolved = token or os.environ.get(token_env)
        if not resolved:
            raise ConnectorAPIError(f"Notion token not found. Export {token_env}.")
        self.token_env = token_env
        self.notion_version = notion_version
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self._headers = {
            "Authorization": f"Bearer {resolved}",
            "Notion-Version": notion_version,
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            response = self.session.request(
                method,
                url,
                headers=self._headers,
                json=json_body,
                params=params,
                timeout=self.timeout,
            )
            if response.status_code in {429, 529} and attempt < self.max_retries:
                retry_after = float(response.headers.get("Retry-After") or "1")
                time.sleep(retry_after)
                continue
            if response.status_code >= 400:
                raise ConnectorAPIError(f"Notion API {response.status_code}: {response.text}")
            payload = response.json()
            if not isinstance(payload, dict):
                raise ConnectorAPIError("Notion API returned a non-object response.")
            return payload
        raise ConnectorAPIError("Notion API retry budget exhausted.")

    def iter_data_source_pages(
        self,
        data_source_id: str,
        *,
        page_size: int = 100,
        max_pages: Optional[int] = None,
        filter_payload: Optional[dict[str, Any]] = None,
        sorts: Optional[list[dict[str, Any]]] = None,
    ) -> Iterator[dict[str, Any]]:
        cursor: Optional[str] = None
        fetched_pages = 0
        while True:
            body: dict[str, Any] = {"page_size": min(max(page_size, 1), 100)}
            if cursor:
                body["start_cursor"] = cursor
            if filter_payload:
                body["filter"] = filter_payload
            if sorts:
                body["sorts"] = sorts
            payload = self.request("POST", f"/v1/data_sources/{data_source_id}/query", json_body=body)
            for page in payload.get("results", []) or []:
                if isinstance(page, dict):
                    yield page
            fetched_pages += 1
            if max_pages is not None and fetched_pages >= max_pages:
                break
            if not payload.get("has_more"):
                break
            cursor = payload.get("next_cursor")
            if not cursor:
                break

    def iter_block_children(
        self,
        block_id: str,
        *,
        page_size: int = 100,
        recursive: bool = True,
        max_depth: int = 8,
        _depth: int = 0,
    ) -> Iterator[dict[str, Any]]:
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {"page_size": min(max(page_size, 1), 100)}
            if cursor:
                params["start_cursor"] = cursor
            payload = self.request("GET", f"/v1/blocks/{block_id}/children", params=params)
            for block in payload.get("results", []) or []:
                if not isinstance(block, dict):
                    continue
                yield block
                if recursive and block.get("has_children") and _depth < max_depth:
                    yield from self.iter_block_children(
                        str(block.get("id")),
                        page_size=page_size,
                        recursive=True,
                        max_depth=max_depth,
                        _depth=_depth + 1,
                    )
            if not payload.get("has_more"):
                break
            cursor = payload.get("next_cursor")
            if not cursor:
                break

    def page_content_markdown(self, page_id: str) -> str:
        return blocks_to_markdown(self.iter_block_children(page_id))


def fetch_data_source_records(
    data_source_id: str,
    *,
    token_env: str = "NOTION_TOKEN",
    notion_version: str = DEFAULT_NOTION_VERSION,
    category: str = "notion",
    max_pages: Optional[int] = None,
    include_blocks: bool = True,
) -> list[ConnectorRecord]:
    client = NotionClient(token_env=token_env, notion_version=notion_version)
    records: list[ConnectorRecord] = []
    for page in client.iter_data_source_pages(data_source_id, max_pages=max_pages):
        page_id = str(page.get("id") or "")
        block_text = client.page_content_markdown(page_id) if include_blocks and page_id else ""
        records.append(page_to_record(page, block_text=block_text, category=category))
    return records


def fetch_page_records(
    page_ids: Iterable[str],
    *,
    token_env: str = "NOTION_TOKEN",
    notion_version: str = DEFAULT_NOTION_VERSION,
    category: str = "notion",
    include_blocks: bool = True,
) -> list[ConnectorRecord]:
    client = NotionClient(token_env=token_env, notion_version=notion_version)
    records: list[ConnectorRecord] = []
    for page_id in page_ids:
        page = client.request("GET", f"/v1/pages/{page_id}")
        block_text = client.page_content_markdown(page_id) if include_blocks else ""
        records.append(page_to_record(page, block_text=block_text, category=category))
    return records


__all__ = [
    "ConnectorAPIError",
    "DEFAULT_NOTION_VERSION",
    "NotionClient",
    "block_to_text",
    "blocks_to_markdown",
    "fetch_data_source_records",
    "fetch_page_records",
    "page_metadata",
    "page_title",
    "page_to_record",
]
