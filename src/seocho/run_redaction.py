"""Sanitize diagnostic text without altering connection credentials or answers."""

from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from .run_spec import RunSpec

_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"']+")


def safe_endpoint(uri: str) -> str:
    """Retain the connection identity without userinfo, query or fragment."""
    try:
        parts = urlsplit(uri)
        return urlunsplit(
            (parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", "")
        )
    except ValueError:
        return "[invalid endpoint]"


def redact_diagnostic(spec: RunSpec | None, value: object) -> str:
    """Remove known credentials and URL credentials from failure messages."""
    secrets = {spec.graph_password if spec else ""}
    try:
        parts = urlsplit(spec.graph if spec else "")
        secrets.update((parts.password or "", unquote(parts.password or "")))
        from urllib.parse import parse_qsl

        secrets.update(v for _, v in parse_qsl(parts.query))
    except ValueError:
        pass
    secrets.update(
        v
        for k, v in os.environ.items()
        if k.endswith(("_API_KEY", "_TOKEN", "_PASSWORD", "_SECRET"))
    )
    text = _URL.sub(lambda m: safe_endpoint(m.group()), str(value))
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text
