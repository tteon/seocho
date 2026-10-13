from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class RecorderConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    upstream_url: str
    upstream_api_key: SecretStr | None = Field(default=None, exclude=True)
    directory: Path = Path('outputs/agent-observability')
    cache_namespace: str = 'local-experiment'
    cache_mode: Literal['off', 'read', 'read_write', 'replay_only'] = 'off'
    missing_tags: Literal['reject', 'quarantine'] = 'reject'
    timeout_seconds: float = Field(default=120, gt=0)
    max_request_bytes: int = Field(default=10_000_000, gt=0)

    @model_validator(mode='after')
    def validate_profile(self) -> RecorderConfig:
        from urllib.parse import urlsplit
        url = urlsplit(self.upstream_url)
        if url.scheme not in ('http', 'https') or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ValueError('Use a fixed HTTP upstream without embedded credentials/query/fragment')
        if not self.cache_namespace.strip():
            raise ValueError('Cache namespace must identify the provider/account experiment')
        return self
