from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

from .http_transport import RuntimeHttpTransport


@dataclass(slots=True)
class RemoteClientHelper:
    """Own HTTP transport setup and request dispatch behind the SDK facade."""

    base_url: str
    transport: RuntimeHttpTransport

    @classmethod
    def build(
        cls,
        *,
        base_url: str,
        session: requests.Session,
        timeout: float,
    ) -> "RemoteClientHelper":
        transport = RuntimeHttpTransport(
            base_url=base_url,
            session=session,
            timeout=timeout,
        )
        return cls(base_url=base_url, transport=transport)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.transport.request_json(
            method,
            path,
            json_body=json_body,
            params=params,
        )
