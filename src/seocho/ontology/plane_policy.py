"""Explicit admission policy for canonical graph projection.

The policy is deliberately small and side-effect free.  The lifecycle store
remains the authority for active versions and leases; this module makes the
operator-selected *mode* visible to the SDK and prevents a strict mode from
silently using the legacy Python Bolt writer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class ProjectionMode(str, Enum):
    """Canonicality contract selected per ingestion run."""

    DIRECT = "direct"
    SHADOW = "shadow"
    GOVERNED = "governed"
    LOCKDOWN = "lockdown"

    @classmethod
    def parse(cls, value: str | None) -> "ProjectionMode":
        # The mode is deliberately a run/API argument, never a process-global
        # environment switch.  That prevents two workspaces in one process from
        # silently sharing a canonicality contract.
        raw = (value or cls.DIRECT.value).strip().lower()
        try:
            return cls(raw)
        except ValueError as exc:
            allowed = ", ".join(mode.value for mode in cls)
            raise ValueError(f"unknown governance mode {raw!r}; expected one of {allowed}") from exc


class ProjectionPolicyError(RuntimeError):
    """The requested projection cannot meet its declared canonicality mode."""


@dataclass(frozen=True)
class ProjectionDecision:
    """Content-free receipt for traces and projection summaries."""

    mode: str
    allowed: bool
    governance_enforced: bool
    canonical_claim_allowed: bool
    requires_rust_projector: bool
    missing: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["missing"] = list(self.missing)
        return payload


def decide_projection(
    mode: ProjectionMode | str | None,
    *,
    rust_socket: str | None,
    semantic_receipt: Mapping[str, Any] | None,
    admission: Mapping[str, Any] | None,
    graph_model: str = "lpg",
) -> ProjectionDecision:
    """Decide whether a projection path satisfies the selected policy.

    ``shadow`` never grants a canonicality claim, even if its receipt happens
    to be valid.  This makes baseline and shadow experiments impossible to
    mislabel as governed evidence.
    """
    selected = ProjectionMode.parse(mode.value if isinstance(mode, ProjectionMode) else mode)
    missing: list[str] = []
    if not rust_socket:
        missing.append("rust_projector_socket")
    if not semantic_receipt:
        missing.append("semantic_receipt")
    if not admission:
        missing.append("lifecycle_admission")
    if selected is ProjectionMode.LOCKDOWN and graph_model != "lpg":
        missing.append("approved_lpg_projection")

    strict = selected in {ProjectionMode.GOVERNED, ProjectionMode.LOCKDOWN}
    required_missing = tuple(missing) if strict else ()
    if required_missing:
        raise ProjectionPolicyError(
            f"{selected.value} projection rejected; missing " + ", ".join(required_missing)
        )
    return ProjectionDecision(
        mode=selected.value,
        allowed=True,
        governance_enforced=strict,
        canonical_claim_allowed=strict,
        requires_rust_projector=strict,
        missing=tuple(missing),
    )


def projection_trace_receipt(
    decision: ProjectionDecision,
    *,
    semantic_receipt: Mapping[str, Any] | None,
    admission: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return stable identities only; never trace paths or raw RDF."""
    return {
        **decision.to_dict(),
        "rdf_bundle_sha256": (semantic_receipt or {}).get("rdf_bundle_sha256"),
        "rdf_data_graph_sha256": (semantic_receipt or {}).get("rdf_data_graph_sha256"),
        "agent_profile_sha256": (semantic_receipt or {}).get("agent_profile_sha256"),
        "projection_receipt_sha256": (semantic_receipt or {}).get("projection_receipt_sha256"),
        "lease_id": (admission or {}).get("lease_id"),
        "generation": (admission or {}).get("generation"),
        "epoch": (admission or {}).get("epoch"),
        "fencing_token": (admission or {}).get("fencing_token"),
    }
