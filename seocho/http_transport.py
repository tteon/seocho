from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests

from .exceptions import SeochoConnectionError, SeochoHTTPError


class RuntimeHttpTransport:
    """Thin HTTP transport wrapper for the public Seocho facade."""

    def __init__(
        self,
        *,
        base_url: str,
        session: requests.Session,
        timeout: float,
    ) -> None:
        self.base_url = base_url
        self.session = session
        self.timeout = timeout

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self.session.request(
                method=method,
                url=url,
                json=json_body,
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SeochoConnectionError(f"Could not reach SEOCHO at {url}: {exc}") from exc

        if response.status_code >= 400:
            detail: Any
            try:
                payload = response.json()
                detail = payload.get("detail", payload)
            except ValueError:
                detail = response.text
            raise SeochoHTTPError(status_code=response.status_code, path=path, detail=detail)

        try:
            payload = response.json()
        except ValueError as exc:
            raise SeochoConnectionError(f"SEOCHO returned invalid JSON for {path}") from exc

        if not isinstance(payload, dict):
            raise SeochoConnectionError(f"SEOCHO returned unexpected payload for {path}")
        return payload
