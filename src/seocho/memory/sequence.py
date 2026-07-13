"""Versioned causal-order contracts for scalable agent memory.

The v1 :class:`CausalToken` is a workspace-wide total order.  The contracts in
this module deliberately model a partial order: sequence values are comparable
only inside the same domain and shard.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .models import workspace_token


class SequenceMode(str, Enum):
    STRICT_WORKSPACE = "strict_workspace"
    LEASED_DOMAIN = "leased_domain"
    SHARDED_DOMAIN = "sharded_domain"


@dataclass(frozen=True, slots=True)
class SequencePolicy:
    mode: SequenceMode = SequenceMode.STRICT_WORKSPACE
    shards: int = 1
    lease_size: int = 1

    def __post_init__(self) -> None:
        if self.shards < 1 or self.shards > 4096:
            raise ValueError("shards must be between 1 and 4096")
        if self.lease_size < 1 or self.lease_size > 1_000_000:
            raise ValueError("lease_size must be between 1 and 1000000")
        if self.mode is SequenceMode.STRICT_WORKSPACE and (
            self.shards != 1 or self.lease_size != 1
        ):
            raise ValueError("strict_workspace requires one shard and lease_size=1")
        if self.mode is SequenceMode.LEASED_DOMAIN and self.shards != 1:
            raise ValueError("leased_domain requires one shard")

    def shard_for(self, aggregate_id: str) -> int:
        value = aggregate_id.strip()
        if not value:
            raise ValueError("aggregate_id is required")
        if self.mode is not SequenceMode.SHARDED_DOMAIN:
            return 0
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % self.shards


@dataclass(frozen=True, order=True, slots=True)
class CausalPosition:
    domain: str
    shard: int
    sequence: int

    def __post_init__(self) -> None:
        if not self.domain.strip():
            raise ValueError("causal domain is required")
        if self.shard < 0 or self.sequence < 0:
            raise ValueError("shard and sequence must be non-negative")

    @property
    def key(self) -> tuple[str, int]:
        return self.domain, self.shard


@dataclass(frozen=True, slots=True)
class CausalFrontier:
    workspace_token: str
    positions: tuple[CausalPosition, ...]

    def __post_init__(self) -> None:
        if not self.workspace_token.strip():
            raise ValueError("workspace_token is required")
        keys = [position.key for position in self.positions]
        if len(keys) != len(set(keys)):
            raise ValueError("a frontier may contain one position per domain and shard")
        if tuple(sorted(self.positions)) != self.positions:
            raise ValueError("frontier positions must be sorted")

    @classmethod
    def for_workspace(
        cls, workspace_id: str, *positions: CausalPosition
    ) -> "CausalFrontier":
        return cls(workspace_token(workspace_id), tuple(sorted(positions)))

    def assert_workspace(self, workspace_id: str) -> None:
        if self.workspace_token != workspace_token(workspace_id):
            raise ValueError("causal frontier belongs to another workspace")

    def satisfied_by(self, watermarks: Mapping[tuple[str, int], int]) -> bool:
        return all(
            int(watermarks.get(position.key, -1)) >= position.sequence
            for position in self.positions
        )

    def merge(self, other: "CausalFrontier") -> "CausalFrontier":
        if self.workspace_token != other.workspace_token:
            raise ValueError("cannot merge frontiers from different workspaces")
        merged = {position.key: position for position in self.positions}
        for position in other.positions:
            prior = merged.get(position.key)
            if prior is None or position.sequence > prior.sequence:
                merged[position.key] = position
        return CausalFrontier(self.workspace_token, tuple(sorted(merged.values())))

    def serialize(self) -> str:
        payload = {
            "workspace": self.workspace_token,
            "positions": [
                {
                    "domain": position.domain,
                    "shard": position.shard,
                    "sequence": position.sequence,
                }
                for position in self.positions
            ],
        }
        return "memory.v2:" + json.dumps(payload, sort_keys=True, separators=(",", ":"))


__all__ = [
    "CausalFrontier",
    "CausalPosition",
    "SequenceMode",
    "SequencePolicy",
]
