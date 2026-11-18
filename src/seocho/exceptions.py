from __future__ import annotations

from typing import Any, Optional


class SeochoError(Exception):
    """Base exception for the SEOCHO client."""


class SeochoConnectionError(SeochoError):
    """Raised when the client cannot reach the SEOCHO backend."""


class SeochoCredentialError(SeochoError):
    """Raised when an LLM provider credential is missing.

    Replaces the upstream ``openai`` client's ``OPENAI_API_KEY``-branded
    construction error with a message that names the actual provider and
    the environment variable(s) SEOCHO resolves for it.
    """


class SeochoHTTPError(SeochoError):
    """Raised when the SEOCHO backend returns an HTTP error."""

    def __init__(self, *, status_code: int, path: str, detail: Any) -> None:
        self.status_code = status_code
        self.path = path
        self.detail = detail
        super().__init__(f"SEOCHO API error {status_code} for {path}: {detail}")


class SeochoProviderTerminalError(SeochoError):
    """Raised when an LLM provider rejects a request with a terminal status.

    HTTP 401/403 (credentials or permissions) and 402 (payment required)
    cannot be fixed by resending, so the completion retry ladder stops on
    the first one instead of replaying every request variant.

    The original provider exception is available as ``provider_error`` but
    is deliberately NOT chained (``__cause__``/``__context__`` suppressed):
    its message embeds the raw response body, which for 402 includes billing
    fields that must not leak into logged tracebacks.
    """

    def __init__(self, *, status_code: int, code: str, provider: str, model: str) -> None:
        self.provider_error: Optional[BaseException] = None
        self.request_id: Optional[str] = None
        self.status_code = status_code
        self.code = code
        self.provider = provider
        self.model = model
        super().__init__(
            f"LLM provider '{provider}' rejected model '{model}' with HTTP "
            f"{status_code} ({code}); not retrying. Check the provider "
            f"credentials, permissions, and billing state."
        )
