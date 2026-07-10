"""Small, non-customer coordination records suitable for etcd."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


_ALLOWED_KINDS = {
    "active_policy",
    "projection_watermark",
    "worker_lease",
    "shard_owner",
    "fencing_token",
}
_FORBIDDEN_KEY_PARTS = {
    "user",
    "customer_id",
    "wallet",
    "address",
    "transaction",
    "risk_signal",
    "beneficiary",
}


def workspace_token(workspace_id: str) -> str:
    if not workspace_id.strip():
        raise ValueError("workspace_id is required")
    return hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class CoordinationRecord:
    """Metadata pointer for etcd; never a user or transaction record."""

    kind: str
    key: str
    value: Mapping[str, Any]

    def validate(self) -> None:
        if self.kind not in _ALLOWED_KINDS:
            raise ValueError(f"unsupported coordination kind: {self.kind}")
        normalized = self.key.lower()
        leaked = sorted(part for part in _FORBIDDEN_KEY_PARTS if part in normalized)
        if leaked:
            raise ValueError("customer data is forbidden in coordination keys")
        encoded = json.dumps(dict(self.value), sort_keys=True, default=str)
        if len(encoded.encode("utf-8")) > 8192:
            raise ValueError("coordination value exceeds 8 KiB")
        forbidden_values = _FORBIDDEN_KEY_PARTS.intersection(self.value)
        if forbidden_values:
            raise ValueError("customer data is forbidden in coordination values")


def active_policy_record(
    *, workspace_id: str, policy_id: str, policy_version: str
) -> CoordinationRecord:
    record = CoordinationRecord(
        kind="active_policy",
        key=f"/seocho/workspaces/{workspace_token(workspace_id)}/policy/active",
        value={"policy_id": policy_id, "policy_version": policy_version},
    )
    record.validate()
    return record

def projection_watermark_record(
    *, workspace_id: str, projection: str, watermark: str
) -> CoordinationRecord:
    record = CoordinationRecord(
        kind="projection_watermark",
        key=(
            f"/seocho/workspaces/{workspace_token(workspace_id)}"
            f"/projections/{projection}/watermark"
        ),
        value={"projection": projection, "watermark": watermark},
    )
    record.validate()
    return record
