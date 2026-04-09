from __future__ import annotations

from typing import Any


class SeochoError(Exception):
    """Base exception for the SEOCHO client."""


class SeochoConnectionError(SeochoError):
    """Raised when the client cannot reach the SEOCHO backend."""


class SeochoHTTPError(SeochoError):
    """Raised when the SEOCHO backend returns an HTTP error."""

    def __init__(self, *, status_code: int, path: str, detail: Any) -> None:
        self.status_code = status_code
        self.path = path
        self.detail = detail
        super().__init__(f"SEOCHO API error {status_code} for {path}: {detail}")
