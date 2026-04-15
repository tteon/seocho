from __future__ import annotations

import os
from typing import Optional


def local_llm_api_key_error(llm: str, api_key: Optional[str] = None) -> str:
    """Return a human-readable local benchmark API-key error, or an empty string."""

    provider = (llm.split("/", 1)[0] if "/" in llm else "openai").strip().lower() or "openai"
    try:
        from seocho.store.llm import get_provider_spec

        spec = get_provider_spec(provider)
    except Exception as exc:
        return f"Unsupported local benchmark LLM provider '{provider}': {exc}"

    if api_key or os.getenv(spec.api_key_env):
        return ""
    return (
        f"{spec.api_key_env} is required for local benchmark llm='{llm}'. "
        f"Set {spec.api_key_env} or pass --api-key. "
        "Local benchmark runs create the SDK client in-process, so provider "
        "credentials must be available in this shell."
    )
