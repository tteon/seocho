"""Slack connector materialization helpers."""

from __future__ import annotations

import os
import re
import time
from typing import Any, Iterable, Iterator, Mapping, Optional

import requests

from .records import ConnectorRecord, stable_record_id

_MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")
_CHANNEL_RE = re.compile(r"<#([A-Z0-9]+)(?:\|([^>]+))?>")
_LINK_RE = re.compile(r"<(https?://[^>|]+)(?:\|([^>]+))?>")


class ConnectorAPIError(RuntimeError):
    """Raised when Slack returns an unsuccessful API response."""


def render_slack_text(text: str) -> str:
    """Render common Slack mrkdwn entity wrappers as readable plain text."""

    rendered = _MENTION_RE.sub(r"@\1", str(text or ""))
    rendered = _CHANNEL_RE.sub(lambda m: f"#{m.group(2) or m.group(1)}", rendered)
    rendered = _LINK_RE.sub(lambda m: m.group(2) or m.group(1), rendered)
    return rendered


def message_to_record(
    message: Mapping[str, Any],
    *,
    channel_id: str,
    team_id: str = "",
    channel_name: str = "",
    category: str = "slack",
) -> ConnectorRecord:
    ts = str(message.get("ts") or "")
    thread_ts = str(message.get("thread_ts") or ts)
    user = str(message.get("user") or message.get("bot_id") or "")
    content = render_slack_text(str(message.get("text") or "")).strip()
    if not content:
        content = f"Slack message {ts}"
    external_id = f"{team_id}:{channel_id}:{ts}" if team_id else f"{channel_id}:{ts}"
    metadata = {
        "team_id": team_id,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "message_ts": ts,
        "thread_ts": thread_ts,
        "author_id": user,
        "reply_count": message.get("reply_count", 0),
        "message_type": message.get("type"),
        "message_subtype": message.get("subtype"),
    }
    title_channel = f"#{channel_name}" if channel_name else channel_id
    return ConnectorRecord(
        id=stable_record_id("slack", external_id, content),
        content=content,
        provider="slack",
        source_kind="slack_message",
        category=category,
        title=f"{title_channel} {ts}".strip(),
        created_at=ts,
        updated_at=str(message.get("edited", {}).get("ts") if isinstance(message.get("edited"), Mapping) else ""),
        metadata=metadata,
    )


def thread_to_record(
    messages: Iterable[Mapping[str, Any]],
    *,
    channel_id: str,
    team_id: str = "",
    channel_name: str = "",
    category: str = "slack",
) -> ConnectorRecord:
    items = [message for message in messages if isinstance(message, Mapping)]
    if not items:
        raise ValueError("thread_to_record requires at least one message.")
    root = items[0]
    thread_ts = str(root.get("thread_ts") or root.get("ts") or "")
    rendered: list[str] = []
    author_ids: list[str] = []
    for message in items:
        author = str(message.get("user") or message.get("bot_id") or "")
        if author and author not in author_ids:
            author_ids.append(author)
        text = render_slack_text(str(message.get("text") or "")).strip()
        if text:
            rendered.append(f"{author or 'unknown'}: {text}")
    content = "\n".join(rendered) or f"Slack thread {thread_ts}"
    external_id = f"{team_id}:{channel_id}:thread:{thread_ts}" if team_id else f"{channel_id}:thread:{thread_ts}"
    title_channel = f"#{channel_name}" if channel_name else channel_id
    return ConnectorRecord(
        id=stable_record_id("slack", external_id, content),
        content=content,
        provider="slack",
        source_kind="slack_thread",
        category=category,
        title=f"{title_channel} thread {thread_ts}".strip(),
        created_at=str(root.get("ts") or ""),
        updated_at=str(items[-1].get("ts") or ""),
        metadata={
            "team_id": team_id,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "thread_ts": thread_ts,
            "message_count": len(items),
            "author_ids": author_ids,
        },
    )


class SlackClient:
    """Small read-only Slack Web API client."""

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        token_env: str = "SLACK_BOT_TOKEN",
        base_url: str = "https://slack.com/api",
        session: Optional[requests.Session] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        resolved = token or os.environ.get(token_env)
        if not resolved:
            raise ConnectorAPIError(f"Slack token not found. Export {token_env}.")
        self.token_env = token_env
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self._headers = {"Authorization": f"Bearer {resolved}"}

    def api(self, method: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = f"{self.base_url}/{method}"
        for attempt in range(self.max_retries + 1):
            response = self.session.get(
                url,
                headers=self._headers,
                params=params or {},
                timeout=self.timeout,
            )
            if response.status_code == 429 and attempt < self.max_retries:
                retry_after = float(response.headers.get("Retry-After") or "1")
                time.sleep(retry_after)
                continue
            if response.status_code >= 400:
                raise ConnectorAPIError(f"Slack API HTTP {response.status_code}: {response.text}")
            payload = response.json()
            if not isinstance(payload, dict):
                raise ConnectorAPIError("Slack API returned a non-object response.")
            if not payload.get("ok", False):
                raise ConnectorAPIError(f"Slack API error for {method}: {payload.get('error', 'unknown')}")
            return payload
        raise ConnectorAPIError("Slack API retry budget exhausted.")

    def iter_conversation_history(
        self,
        channel_id: str,
        *,
        limit: int = 15,
        max_pages: Optional[int] = None,
        oldest: str = "",
        latest: str = "",
    ) -> Iterator[dict[str, Any]]:
        cursor = ""
        fetched_pages = 0
        while True:
            params: dict[str, Any] = {
                "channel": channel_id,
                "limit": min(max(limit, 1), 200),
            }
            if cursor:
                params["cursor"] = cursor
            if oldest:
                params["oldest"] = oldest
            if latest:
                params["latest"] = latest
            payload = self.api("conversations.history", params)
            for message in payload.get("messages", []) or []:
                if isinstance(message, dict):
                    yield message
            fetched_pages += 1
            if max_pages is not None and fetched_pages >= max_pages:
                break
            cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor:
                break

    def iter_conversation_replies(
        self,
        channel_id: str,
        thread_ts: str,
        *,
        limit: int = 15,
        max_pages: Optional[int] = None,
    ) -> Iterator[dict[str, Any]]:
        cursor = ""
        fetched_pages = 0
        while True:
            params: dict[str, Any] = {
                "channel": channel_id,
                "ts": thread_ts,
                "limit": min(max(limit, 1), 200),
            }
            if cursor:
                params["cursor"] = cursor
            payload = self.api("conversations.replies", params)
            for message in payload.get("messages", []) or []:
                if isinstance(message, dict):
                    yield message
            fetched_pages += 1
            if max_pages is not None and fetched_pages >= max_pages:
                break
            cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor:
                break


def fetch_channel_records(
    channel_ids: Iterable[str],
    *,
    token_env: str = "SLACK_BOT_TOKEN",
    team_id: str = "",
    channel_name: str = "",
    category: str = "slack",
    limit: int = 15,
    max_pages: Optional[int] = None,
    include_threads: bool = False,
) -> list[ConnectorRecord]:
    client = SlackClient(token_env=token_env)
    records: list[ConnectorRecord] = []
    seen_threads: set[str] = set()
    for channel_id in channel_ids:
        for message in client.iter_conversation_history(channel_id, limit=limit, max_pages=max_pages):
            thread_ts = str(message.get("thread_ts") or message.get("ts") or "")
            if include_threads and int(message.get("reply_count") or 0) > 0 and thread_ts not in seen_threads:
                replies = list(
                    client.iter_conversation_replies(
                        channel_id,
                        thread_ts,
                        limit=limit,
                        max_pages=max_pages,
                    )
                )
                records.append(
                    thread_to_record(
                        replies or [message],
                        channel_id=channel_id,
                        team_id=team_id,
                        channel_name=channel_name,
                        category=category,
                    )
                )
                seen_threads.add(thread_ts)
            else:
                records.append(
                    message_to_record(
                        message,
                        channel_id=channel_id,
                        team_id=team_id,
                        channel_name=channel_name,
                        category=category,
                    )
                )
    return records


__all__ = [
    "ConnectorAPIError",
    "SlackClient",
    "fetch_channel_records",
    "message_to_record",
    "render_slack_text",
    "thread_to_record",
]
